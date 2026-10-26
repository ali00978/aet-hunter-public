"""A.E.T CARS: beperkte test van publiek toegankelijke advertentiegegevens.
Geen blokkade-omzeiling; controleer gebruiksvoorwaarden voor structurele inzet.
"""
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from car_triage import assess, extra_checks, deal_watchlist as base_deal_watchlist
from seller_filter import seller_priority as base_seller_priority
from marktplaats_market import market_comparison, quality_gate, listing_id, minimum_profit, identity as market_identity
from marktplaats_data import seller_evidence, photo_urls, public_listing_config, search_page_records, vehicle_facts, parse_mileage, price_from_cents
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

BASE = 'https://www.marktplaats.nl'
# Only the 16 approved makes. No Volvo, Alfa Romeo or other brands.
ALLOWED_BRANDS = ('toyota', 'suzuki', 'kia', 'hyundai', 'renault', 'opel',
                  'peugeot', 'citroen', 'fiat', 'volkswagen', 'ford', 'skoda',
                  'seat', 'bmw', 'audi', 'mercedes-benz')
PRIORITY_QUERIES = ['toyota aygo', 'toyota yaris', 'suzuki alto', 'suzuki swift',
                    'kia picanto', 'kia rio', 'hyundai i10', 'hyundai i20',
                    'renault clio', 'renault twingo', 'opel corsa', 'opel astra',
                    'peugeot 107', 'peugeot 206', 'peugeot 207', 'peugeot 208',
                    'citroen c1', 'citroen c3', 'fiat punto', 'fiat panda',
                    'volkswagen polo', 'ford fiesta', 'skoda fabia', 'seat ibiza']
OTHER_QUERIES = ['toyota corolla', 'suzuki splash', 'renault scenic',
                 'opel agila', 'peugeot 307', 'fiat 500', 'volkswagen golf',
                 'ford focus', 'skoda roomster', 'seat leon',
                 'bmw 1 serie', 'bmw 3 serie', 'audi a3', 'audi a4',
                 'mercedes-benz a klasse', 'mercedes-benz b klasse']
QUERIES = PRIORITY_QUERIES + OTHER_QUERIES

def approved_listing_brand(url):
    """Use the Marktplaats vehicle category, not an ambiguous title keyword."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        # A path that resembles a Marktplaats listing is not enough: pending
        # state and historical references are not trusted network destinations.
        if (parsed.scheme != 'https' or
                parsed.netloc not in ('www.marktplaats.nl', 'marktplaats.nl')):
            return False
        parts = parsed.path.rstrip('/').lower().split('/')
    except ValueError:
        return False
    if len(parts) < 4 or parts[1] != 'v' or parts[2] != 'auto-s':
        return False
    brand = parts[3].replace('_', '-')
    # Only actual advert IDs belong in the queue; category/SEO links must
    # not consume a detail request or become a bogus market reference.
    return (brand in ALLOWED_BRANDS and
            bool(re.fullmatch(r'[ma][0-9]+(?:-.*)?', parts[4]) if len(parts) == 5 else False))

TARGET_RADIUS_KM = 75  # Target only; NOT enforced until a verified location filter exists.
MAX_PER_RUN = 110  # Adaptive ceiling; stop immediately on server restrictions.
REQUEST_DELAY_SECONDS = 5
MAX_RUN_SECONDS = 1000
MAX_TOTAL_REQUESTS = 145  # Bounded public requests per two-hour scan; existing backoff remains mandatory.
PER_QUERY = 70
SEARCHES_PER_RUN = 28
# Extra queries are ordinary public search terms; no undocumented parameters.
VALUE_TERMS = ('goedkoop', 'spoed', 'moet weg', 'inruil', 'apk', 'particulier', 'wegens', 'opruiming')
VALUE_SEARCHES_PER_RUN = 6
# Year-qualified model searches help expose stock hidden behind saturated
# generic search results. They are ordinary public keyword searches.
YEAR_SEARCH_TERMS = ('2010', '2012', '2014', '2016')
DISCOVERY_TERMS = VALUE_TERMS + YEAR_SEARCH_TERMS
MIN_YEAR = 2010
MAX_PURCHASE_MILEAGE_KM = 330_000  # Purchase ceiling, not a parser or comparison-reference ceiling.
OLDER_MAX_PRICE = 1250
EXCLUDE_TERMS = ('gevraagd', 'gezocht', 'inkoop', 'financial lease', 'lease voor', 'per maand', 'p/mnd', 'p.mnd', 'p/m', 'maandbedrag', 'aanbetaling', 'termijnbedrag')
def configured_max_buy_price(raw):
    """Invalid settings must not crash the scanner or silently raise the budget."""
    try:
        value = int(str(raw).strip())
        return value if 350 <= value <= 12000 else 3000
    except (TypeError, ValueError, OverflowError):
        return 3000


MAX_BUY_PRICE = configured_max_buy_price(os.getenv('MAX_BUY_PRICE', '3000'))
# Asking-price review thresholds, not resale valuations or guaranteed profit.
MODEL_REVIEW_PRICES = {'renault clio': 2500, 'fiat punto': 700}
STATUS = Path('marktplaats_status.json')
HOTLIST = Path('marktplaats_koopkansen.json')
BIDLIST = Path('marktplaats_biedkansen.json')
OUTPUT = Path('marktplaats_proefresultaten.json')
ARCHIVE = Path('marktplaats_vergelijkingen.json')
HISTORY = Path('marktplaats_seen.json')
LAST_CHECKED = Path('marktplaats_last_checked.json')
COOLDOWN = Path('marktplaats_cooldown.json')
GLOBAL_BACKOFF_KEY = '__global_backoff_until__'
BLOCK_COUNT_KEY = '__block_count__'
PENDING = Path('marktplaats_wachtrij.json')
HEADERS = {'User-Agent': 'AETCarsResearchTest/1.0', 'Accept': 'text/html'}

def write_json_state(path, data):
    """Atomically replace one scan file; never expose partially written JSON.

    Stage next to the destination (same filesystem), flush before replacement,
    and clean up the temporary file even when replacement fails.
    """
    destination = Path(path)
    # Compact serialization preserves every field while reducing large rolling
    # comparison archives below the encrypted checkpoint's per-file byte cap.
    # Pretty-print whitespace is not part of the persisted JSON schema.
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=destination.parent,
                prefix='.' + destination.name + '.', suffix='.tmp',
                delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class PageParser(HTMLParser):
    """Read listing links and their text without losing nested/void HTML tags."""
    def __init__(self):
        super().__init__()
        self.links = []
        self.link_texts = {}
        self.current_link = None
        self.link_parts = []
        self.link_attributes = {}
        self.meta = {}
        self.ld = []
        self.in_ld = False
        self.parts = []
        self.card_stack = []
        self.card_texts = {}
        self.detail_blocks = {}
        self.detail_stack = []
        self.scripts = []
        self.script_parts = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'script':
            self.script_parts = []
        void = tag in ('img', 'input', 'meta', 'link', 'br', 'hr', 'source', 'area', 'wbr')
        for block in self.detail_stack:
            if not void:
                block['depth'] += 1
            if tag in ('br', 'p', 'div', 'li'):
                block['parts'].append('\n')
        classes = str(a.get('class') or '').split()
        kind = ('description' if 'Description-module-description' in classes else
                'location' if 'SellerLocationSection-module-locationName' in classes else
                'apk' if 'QualityMark-module-title' in classes else None)
        if kind:
            self.detail_stack.append({'kind': kind, 'depth': 1, 'parts': []})
        # Search-card prices are often siblings of the listing anchor.
        # Capture article/list-item text only when it belongs to one listing.
        if self.card_stack and tag not in ('img', 'input', 'meta', 'link', 'br', 'hr', 'source', 'area', 'wbr'):
            for card in self.card_stack:
                card['depth'] += 1
        if tag in ('article', 'li'):
            self.card_stack.append({'tag': tag, 'depth': 1, 'links': set(), 'parts': []})
        if tag == 'a' and a.get('href'):
            for card in self.card_stack:
                card['links'].add(a['href'])
        if self.card_stack:
            for card in self.card_stack:
                card['parts'].extend(str(a[k]) for k in ('data-price', 'aria-label') if a.get(k))
                # Price microdata can live in a sibling <meta itemprop="price">.
                if str(a.get('itemprop') or '').lower() == 'price' and a.get('content'):
                    card['parts'].append('€ ' + str(a['content']))
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
            # An anchor's text ends at </a>, regardless of nested spans/images.
            self._finish_link()
            self.current_link = a['href']
            self.link_attributes[self.current_link] = {k: a[k] for k in ('title', 'aria-label', 'data-price') if a.get(k)}
            self.link_parts.extend(str(a[k]) for k in ('title', 'aria-label', 'data-price') if a.get(k))
        if self.current_link is not None and tag != 'a':
            self.link_parts.extend(str(a[k]) for k in ('alt', 'title', 'aria-label', 'data-price') if a.get(k))
        if tag == 'meta':
            k = a.get('property') or a.get('name')
            if k and a.get('content'):
                self.meta[k.lower()] = a['content']
        if tag == 'script' and a.get('type', '').lower() == 'application/ld+json':
            self.in_ld = True
            self.parts = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.script_parts is not None:
            self.script_parts.append(data)
        for block in self.detail_stack:
            block['parts'].append(data)
        for card in self.card_stack:
            card['parts'].append(data)
        if self.current_link is not None:
            self.link_parts.append(data)
        if self.in_ld:
            self.parts.append(data)

    def _finish_link(self):
        if self.current_link is not None:
            self.link_texts[self.current_link] = ' '.join(self.link_parts)
            self.current_link = None
            self.link_parts = []

    def handle_endtag(self, tag):
        if tag == 'script' and self.script_parts is not None:
            self.scripts.append(''.join(self.script_parts))
            self.script_parts = None
        if tag not in ('img', 'input', 'meta', 'link', 'br', 'hr', 'source', 'area', 'wbr'):
            for block in self.detail_stack:
                block['depth'] -= 1
                if tag in ('p', 'div', 'li'):
                    block['parts'].append('\n')
            while self.detail_stack and self.detail_stack[-1]['depth'] <= 0:
                block = self.detail_stack.pop()
                value = '\n'.join(' '.join(line.split()) for line in ''.join(block['parts']).splitlines() if line.strip())
                if value and (block['kind'] != 'apk' or value.lower().startswith('apk')):
                    self.detail_blocks.setdefault(block['kind'], []).append(value)
        if tag not in ('img', 'input', 'meta', 'link', 'br', 'hr', 'source', 'area', 'wbr'):
            for card in self.card_stack:
                card['depth'] -= 1
        while self.card_stack and self.card_stack[-1]['depth'] <= 0:
            card = self.card_stack.pop()
            listing_links = {normalize_link(h) for h in card['links']}
            listing_links.discard(None)
            if len(listing_links) == 1:
                link = next(iter(listing_links))
                text = ' '.join(card['parts'])
                if len(text) > len(self.card_texts.get(link, '')):
                    self.card_texts[link] = text
        if tag == 'a':
            self._finish_link()
        if tag == 'script' and self.in_ld:
            self.ld.append(''.join(self.parts))
            self.in_ld = False


def retry_after_seconds(headers, now_ts):
    value = str((headers or {}).get('Retry-After') or '').strip()
    if value.isdigit():
        return int(value)
    try:
        date = parsedate_to_datetime(value)
        return max(0, date.timestamp() - now_ts) if date.tzinfo else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def finite_numeric_state(value):
    """Reject invalid JSON numbers, including enormous integers that overflow float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError, TypeError):
        return False


def persist_restriction(error, cooldown, now_ts):
    old_count = cooldown.get(BLOCK_COUNT_KEY, 0)
    count = min(6, max(0, int(old_count) if finite_numeric_state(old_count) else 0) + 1)
    base = 21600 if error.code == 403 else 1800 if error.code == 503 else 7200
    wait = max(min(86400, base * 2 ** (count - 1)), retry_after_seconds(error.headers, now_ts))
    cooldown[BLOCK_COUNT_KEY] = count
    cooldown[GLOBAL_BACKOFF_KEY] = now_ts + wait
    return now_ts + wait


def checkpoint_restriction(error, cooldown, now_ts):
    """Durably record a block before any further scan processing can fail."""
    until = persist_restriction(error, cooldown, now_ts)
    write_json_state(COOLDOWN, cooldown)
    return until


def adaptive_detail_budget(previous):
    """Malformed historic scan metrics must never stop a new scan."""
    if not isinstance(previous, dict):
        previous = {}
    raw = previous.get('detail_request_budget', 65)
    budget = int(raw) if finite_numeric_state(raw) else 65
    budget = min(MAX_PER_RUN, max(0, budget))
    if previous.get('blocked_this_run') or previous.get('global_backoff_active'):
        return 40
    def safe_counter(value):
        return value if (finite_numeric_state(value)
                         and value >= 0) else 0
    errors = (safe_counter(previous.get('detail_errors')) +
              safe_counter(previous.get('search_errors')))
    attempted = safe_counter(previous.get('requests_attempted_total'))
    if errors > max(2, attempted * .1):
        return max(25, min(MAX_PER_RUN, budget - 20))
    return min(MAX_PER_RUN, max(25, budget + 20))


def load_pending(now):
    try:
        rows = json.loads(PENDING.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get('url'), str):
            continue
        url = normalize_link(row['url'])
        if not url or not approved_listing_brand(url):
            continue
        try:
            checked = datetime.fromisoformat(str(row.get('discovered_at')).replace('Z', '+00:00'))
            if checked.tzinfo is None or not timedelta(0) <= now - checked <= timedelta(hours=24):
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        priority = row.get('priority')
        if (not isinstance(priority, list) or len(priority) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and finite_numeric_state(v) for v in priority)):
            priority = [2, 0]
        result[url] = {**row, 'url': url, 'priority': priority}
    return result


def obvious_nonrunning_search_card(fact):
    """Use only high-certainty title claims to deprioritize broken vehicles.

    This is a scheduling rule, never a diagnosis or final exclusion.
    """
    title = str((fact or {}).get('title') or '')
    return bool(re.search(
        r'(?i)\b(?:start niet|rijdt niet|niet rijdend|motor kapot|'
        r'motor defect|motorschade|versnellingsbak kapot|'
        r'loopt niet|koppakking defect)\b', title))


def over_purchase_mileage_limit(item, card_text=''):
    """Known >330k odometer excludes purchase intent, not pricing references.

    Missing mileage remains unknown rather than magically passing verification.
    Search-page text is only a scheduling hint when structured facts have no km.
    """
    if not isinstance(item, dict):
        return False
    # Inspect each independent source. A malformed cached field must not
    # prevent another valid >330k reading from blocking a purchase.
    readings = []
    for key in ('mileage', 'mileage_km', 'search_card_mileage_hint_km'):
        try:
            readings.append(parse_mileage(item.get(key)))
        except (TypeError, ValueError, AttributeError, OverflowError):
            continue
    try:
        readings.append(vehicle_facts(item).get('mileage_km'))
    except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
        pass
    if card_text:
        try:
            readings.append(search_card_signals(card_text).get('mileage_km'))
        except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
            pass
    return any(isinstance(value, (int, float)) and not isinstance(value, bool)
               and finite_numeric_state(value) and value > MAX_PURCHASE_MILEAGE_KM
               for value in readings)


def discovery_priority(fact, fallback=(2, 0)):
    """Scheduling hints only: an unknown seller must still pass detail checks."""
    if obvious_nonrunning_search_card(fact):
        return (7, fact.get('price') or 99999)
    if over_purchase_mileage_limit(fact):
        return (6, fact.get('price') or 99999)
    if fact.get('seller_type') == 'handelaar':
        return (5, fact.get('price') or 99999)
    if fact.get('price_type') in ('MIN_BID', 'FAST_BID'):
        floor = fact.get('minimum_bid_eur') or 0
        if isinstance(floor, (int, float)) and finite_numeric_state(floor) and 0 <= floor <= MAX_BUY_PRICE:
            # A bid floor is not an asking price. Keep bids for research,
            # but prioritize fixed-price cars whose gross margin can be tested.
            return (1, floor) if floor > 0 else (2, 0)
        return (6, 99999)
    if fact.get('price_type') == 'FIXED' and isinstance(fact.get('price'), (int, float)) and 350 <= fact['price'] <= MAX_BUY_PRICE:
        rank = -4 if fact.get('seller_type') == 'particulier' else -2
        if re.search(r'(?i)\b(spoed|verhuizing|moet weg|wegens vertrek)\b', str(fact.get('title') or '')):
            rank -= 0.5
        return (rank, fact['price'])
    return fallback


def pending_discovery_priority(row):
    """Rebuild queue priority from evidence, never from an obsolete raw score.

    Search cards can have a clear public price without structured JSON.
    Preserve that *unverified hint* for scheduling, but never promote bid-only
    or request-only listings into fixed-price opportunities.
    """
    fact = row.get('facts') if isinstance(row, dict) else None
    if not isinstance(fact, dict):
        return (2, 0)
    ranked = discovery_priority(fact, fallback=(2, 0))
    if ranked[0] != 2 or fact.get('price_type') not in (None, 'FIXED'):
        return ranked
    price = fact.get('search_card_price_hint_eur')
    if (isinstance(price, (int, float)) and not isinstance(price, bool)
            and finite_numeric_state(price) and 350 <= price <= MAX_BUY_PRICE
            and not fact.get('reserved') and fact.get('seller_type') != 'handelaar'
            and not is_financing_offer(fact)):
        return (-2 if fact.get('seller_type') == 'particulier' else -1, price)
    return ranked


def merge_pending_search_state(pending, search_facts, priorities):
    """Add queued listings without overwriting newer search-page evidence.

    A listing may change from bid-only to fixed-price (or vice versa).
    Current live search facts and ranking must always win over archived hints.
    """
    # A changed title changes the URL slug but not the Marktplaats listing ID.
    # Do not reintroduce stale price/bid facts under the old URL.
    live_ids = {listing_id(url) for url in set(priorities) | set(search_facts)}
    for url, row in pending.items():
        if listing_id(url) in live_ids and url not in priorities:
            continue
        if url not in priorities:
            priorities[url] = pending_discovery_priority(row)
        fact = row.get('facts') if isinstance(row, dict) else None
        if url not in search_facts and isinstance(fact, dict):
            search_facts[url] = fact


def prepare_pending_rows(found, pending, facts, priorities, last_checked,
                         invalidated_urls, live_seen_ids, now, limit=1000):
    """Keep an accurate bounded queue, refreshing timestamps only on live discovery.

    Use listing IDs when checking revisit history: updated slugs must not
    bypass the same-vehicle cooldown. Preserve first discovery for diagnostics
    while retaining a still-live search card across separate scans.
    """
    now_iso = now.isoformat()
    invalid_ids = {listing_id(url) for url in invalidated_urls}
    recent_checked_ids = set()
    for url, raw in last_checked.items():
        try:
            checked = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
            # Detail checks occur *after* run_now was captured. A timestamp
            # seconds ahead of run_now is still a check in the current run.
            if checked.tzinfo is not None and checked >= now - timedelta(hours=6):
                recent_checked_ids.add(listing_id(url))
        except (ValueError, TypeError, OverflowError):
            continue
    result = []
    used = set()
    # The live slug is authoritative when an older queued URL has the same ID.
    # This also prevents the older queue's price or seller evidence winning.
    live_urls_by_id = {
        listing_id(url): url for url in found
        if listing_id(url) in live_seen_ids
    }
    pending_by_id = {
        listing_id(url): row for url, row in pending.items()
        if isinstance(row, dict)
    }
    for url in found:
        key = listing_id(url)
        if key in live_urls_by_id and url != live_urls_by_id[key]:
            continue
        if key in used or key in invalid_ids or key in recent_checked_ids:
            continue
        used.add(key)
        older = pending.get(url, pending_by_id.get(key, {}))
        if not isinstance(older, dict):
            older = {}
        discovery = older.get('discovered_at')
        first = older.get('first_discovered_at') or discovery or now_iso
        latest = now_iso if key in live_seen_ids else (discovery or now_iso)
        fact = facts.get(url, older.get('facts', {}))
        if not isinstance(fact, dict):
            fact = {}
        score = tuple(priorities.get(url, pending_discovery_priority(older)))
        result.append({
            'url': url, 'priority': list(score),
            'discovered_at': latest,
            'first_discovered_at': first,
            'facts': fact,
            'priority_reason': (
                'Actuele zoekkaart of bewaarde advertentiefacts; '
                'geen gegarandeerde vraagprijs, beschikbaarheid of koopkans')
        })
    result.sort(key=lambda row: (row['priority'], row['discovered_at'], row['url']))
    return result[:limit]


def search_card_sort_key(fact, card_text):
    """Prefer fixed-price purchase prospects before the per-query page cap."""
    fact = fact if isinstance(fact, dict) else {}
    if fact.get('reserved'):
        return (True, (9, 99999))
    kind = fact.get('price_type')
    priority = discovery_priority(fact)
    if over_purchase_mileage_limit(fact, card_text):
        return (False, (6, fact.get('price') or 99999))
    if kind in (None, 'FIXED') and fact.get('seller_type') != 'handelaar':
        price = fact.get('price')
        if not isinstance(price, (int, float)) or isinstance(price, bool) or not finite_numeric_state(price):
            price = search_card_signals(card_text).get('price')
        if (isinstance(price, (int, float)) and finite_numeric_state(price)
                and 350 <= price <= MAX_BUY_PRICE
                and not is_financing_offer({'title': fact.get('title') or '',
                                            'description': card_text, 'price': price})):
            priority = discovery_priority({**fact, 'price_type': 'FIXED', 'price': price},
                                          fallback=(-2, price))
    return (False, priority)


def search_card_vehicle_hints(fact):
    """Return only plausible scheduling hints; never vehicle verification."""
    facts = fact if isinstance(fact, dict) else {}
    verified = vehicle_facts(facts)
    year, km = verified['year_detected'], verified['mileage_km']
    if year is None:
        hint = facts.get('search_card_year_hint')
        if isinstance(hint, int) and not isinstance(hint, bool) and 1990 <= hint <= datetime.now(timezone.utc).year + 1:
            year = hint
    if km is None:
        hint = facts.get('search_card_mileage_hint_km')
        if isinstance(hint, (int, float)) and not isinstance(hint, bool) and finite_numeric_state(hint) and 1000 <= hint <= 700000:
            km = hint
    return year, km, verified['vehicle_data_warnings']


def ranked_search_card(fact, card_text, already_checked=False, market_gap_rank=None):
    """Prefer useful, never-checked >=2010 purchase stock before the page cap.

    This is ordering, never a claim that a search-card asking price or year
    is verified. A missing year is NOT the same as a known pre-2010 car.
    """
    base = search_card_sort_key(fact, card_text)
    if base[0]:
        return (9, 9, 9, 9, base[1])
    if over_purchase_mileage_limit(fact, card_text):
        return (8, 8, 8, 8, base[1])
    fact = fact if isinstance(fact, dict) else {}
    priority = base[1]
    if market_gap_rank is not None:
        priority = (min(priority[0], market_gap_rank), priority[1])
    # Only genuinely affordable, non-dealer fixed-price/hint cards can use
    # vehicle desirability to compete for the limited search result slots.
    purchase_hint = priority[0] < 0
    if not purchase_hint:
        return (1 if priority[0] < 3 else 2, 0, 0, 0, priority)
    year, km, warnings = search_card_vehicle_hints(fact)
    hints = search_card_signals(card_text)
    if year is None:
        year = hints['year']
    if km is None:
        km = hints['mileage_km']
    year_rank = 0 if isinstance(year, int) and year >= MIN_YEAR else (
        1 if year is None else 2)
    km_rank = 0 if isinstance(km, (int, float)) and km <= 250000 else (
        1 if km is None else 2)
    if warnings:
        km_rank = 3
    return (0, year_rank, int(bool(already_checked)), km_rank, priority)


def search_card_market_gap_rank(url, fact, card_text=''):
    """Rank detail requests only, never certify a bargain or sale price."""
    if not isinstance(fact, dict) or fact.get('price_type') != 'FIXED':
        return None
    price = fact.get('price')
    if (not finite_numeric_state(price) or not 350 <= price <= MAX_BUY_PRICE
            or fact.get('reserved') or fact.get('seller_type') == 'handelaar'
            or obvious_nonrunning_search_card(fact)
            or is_financing_offer({**fact, 'description': card_text})):
        return None
    year, km, warnings = search_card_vehicle_hints(fact)
    if year is None or km is None or warnings or km > MAX_PURCHASE_MILEAGE_KM:
        return None
    try:
        comparison = market_comparison({
            **fact, 'url': url, 'year': year, 'mileage_km': km})
        median = comparison.get('median_asking_price_eur')
        if (comparison.get('status') != 'indicatieve_vraagprijzen'
                or comparison.get('sample_size', 0) < 3
                or comparison.get('high_price_dispersion')
                or not finite_numeric_state(median) or median <= 0
                or median - price < 350 or price > median * 0.8):
            return None
    except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
        return None
    return -5 if fact.get('seller_type') == 'particulier' else -3


def non_bid_price_coverage(visible, missing_fixed, missing_unknown):
    """Exclude bids and price-on-request cards from extraction failure rates."""
    expected = visible + missing_fixed + missing_unknown
    if not expected:
        return None, False
    missing = missing_fixed + missing_unknown
    return round(100 * visible / expected, 1), expected >= 20 and missing * 5 >= expected


def historical_affordable_query_yield(base_query, metric_history):
    """Estimate useful search yield, not raw page or dealer listing volume."""
    observations = []
    for snapshot in metric_history[-7:] if isinstance(metric_history, list) else []:
        if not isinstance(snapshot, dict):
            continue
        for query, record in snapshot.items():
            if (not isinstance(record, dict) or record.get('http_status') != 200
                    or not (query == base_query or any(
                        query == base_query + ' ' + suffix for suffix in DISCOVERY_TERMS))):
                continue
            try:
                assessed = max(0, int(record.get('details_assessed') or 0))
                quality = min(assessed, max(0, int(record.get('quality_leads') or 0)))
                observed = max(0, int(record.get('unique_cards_seen') or 0))
                if 'affordable_private_cards' in record:
                    affordable = min(observed, max(0, int(record['affordable_private_cards'])))
                    unseen = min(affordable, max(0, int(
                        record.get('unseen_affordable_private_cards') or 0)))
                    # Strongly prefer unseen affordable stock; correct for query size.
                    score = 3 * (2 * unseen + affordable) / (observed + 5)
                elif assessed:
                    # Legacy scans have quality observations but no budget-specific
                    # counts; do not reward raw numbers of expensive links.
                    score = 0
                else:
                    continue
                if assessed:
                    score += 2 * (quality + .25) / (assessed + 3)
                observations.append(score)
            except (TypeError, ValueError, OverflowError):
                continue
    return sum(observations) / len(observations) if observations else None


def historical_exact_query_yield(query, metric_history):
    """Score one query by affordable unseen cards, not raw advertising volume."""
    observations = []
    for snapshot in metric_history[-7:] if isinstance(metric_history, list) else []:
        row = snapshot.get(query) if isinstance(snapshot, dict) else None
        if not isinstance(row, dict) or row.get('http_status') != 200:
            continue
        if 'affordable_private_cards' not in row:
            continue
        try:
            seen = max(0, int(row.get('unique_cards_seen') or 0))
            affordable = min(seen, max(0, int(row['affordable_private_cards'])))
            unseen = min(affordable, max(0, int(row.get('unseen_affordable_private_cards') or 0)))
        except (ValueError, TypeError, OverflowError):
            continue
        observations.append((2 * unseen + affordable)/(seen + 5))
    return sum(observations)/len(observations) if observations else None


def recently_saturated_search(query, metric_history):
    """Two recent normal searches with no *new* affordable cars are exhausted.

    This is a cue to rotate search wording, not proof that no deals exist.
    Ignore HTTP failures and missing historical metrics.
    """
    observations = []
    for snapshot in metric_history[-6:] if isinstance(metric_history, list) else []:
        row = snapshot.get(query) if isinstance(snapshot, dict) else None
        if not isinstance(row, dict) or row.get('http_status') != 200:
            continue
        try:
            checked = int(row.get('unique_cards_seen') or 0)
            unseen = int(row['unseen_affordable_private_cards'])
        except (ValueError, TypeError, OverflowError, KeyError):
            continue
        if checked >= 5 and unseen >= 0:
            observations.append(unseen)
    return len(observations) >= 2 and all(n == 0 for n in observations[-2:])


def recent_fresh_search_yield(query, metric_history):
    """Average recent unseen affordable hits, discarding older saturated yields."""
    observations = []
    for snapshot in metric_history[-4:] if isinstance(metric_history, list) else []:
        row = snapshot.get(query) if isinstance(snapshot, dict) else None
        if isinstance(row, dict) and row.get('http_status') == 200:
            try:
                seen = int(row.get('unique_cards_seen') or 0)
                fresh = int(row['unseen_affordable_private_cards'])
                if seen > 0 and fresh >= 0:
                    observations.append(fresh / seen)
            except (ValueError, TypeError, OverflowError, KeyError):
                pass
    return sum(observations[-2:]) / len(observations[-2:]) if observations else None


def choose_motivated_query(base, metric_history, rotation):
    """Keep useful broad models; explore suffixes without repeating failed ones."""
    baseline = historical_exact_query_yield(base, metric_history)
    variants = [(historical_exact_query_yield(base+' '+term, metric_history), term)
                for term in DISCOVERY_TERMS]
    unexplored = [term for score, term in variants if score is None]
    # A broad query can look statistically valuable because old cheap listings
    # are still visible on page one. When recent runs found no NEW affordable
    # cards, explore motivated-seller terms instead of repeating that stale set.
    # Every fourth experiment remains broad for fresh arrivals and coverage.
    if recently_saturated_search(base, metric_history) and rotation % 4 != 0:
        fresh_variants = [(recent_fresh_search_yield(base+' '+term, metric_history), term)
                          for _, term in variants]
        active = [(fresh, term) for fresh, term in fresh_variants
                  if fresh is not None and fresh > 0]
        if active:
            return base+' '+max(active, key=lambda x: (x[0], x[1]))[1]
        if unexplored:
            return base+' '+unexplored[rotation % len(unexplored)]
    successful = sorted(((score, term) for score, term in variants
                         if score is not None and score > 0),
                        key=lambda item: (-item[0], item[1]))
    if successful and successful[0][0] > max(.03, (baseline or 0) * 1.12):
        return base+' '+successful[0][1]
    if baseline is not None and baseline > 0:
        return base
    return base+' '+unexplored[rotation % len(unexplored)] if unexplored else base


def affordable_search_pool_split(previous):
    """Use two more compact-car searches when healthy scans run out of leads.

    The total number of searches does not change. Retain at least eight
    secondary-model searches for genuine wider market discovery. Blocked
    scans, invalid statistics and an existing healthy fresh yield cannot
    justify retargeting the search portfolio.
    """
    baseline = (18, SEARCHES_PER_RUN - 18)
    if not isinstance(previous, dict) or previous.get('scan_health') != 'ok':
        return baseline
    if (previous.get('blocked_this_run') or previous.get('global_backoff_active')
            or previous.get('search_errors') or previous.get('detail_errors')):
        return baseline
    fresh = previous.get('unseen_affordable_private_search_cards')
    examined = previous.get('search_cards_considered')
    if (not isinstance(fresh, int) or isinstance(fresh, bool)
            or not isinstance(examined, int) or isinstance(examined, bool)
            or fresh < 0 or examined < 100 or fresh > 2):
        return baseline
    return (20, SEARCHES_PER_RUN - 20)


def adaptive_discovery_variant_count(previous, backlog_ready, ceiling=VALUE_SEARCHES_PER_RUN):
    """Expand keyword experiments only when new affordable stock runs dry.

    Reallocate existing searches; do not increase public request quotas.
    Paused/blocked/error scans are not evidence that generic searches are
    saturated. Missing metrics keep the conservative three-experiment plan.
    """
    ceiling = max(0, min(VALUE_SEARCHES_PER_RUN, int(ceiling)))
    default = min(3, ceiling)
    if not isinstance(previous, dict) or backlog_ready:
        return default
    if (previous.get('scan_health') != 'ok'
            or previous.get('blocked_this_run')
            or previous.get('global_backoff_active')
            or previous.get('search_errors')
            or previous.get('detail_errors')):
        return default
    fresh = previous.get('unseen_affordable_private_search_cards')
    checked = previous.get('search_cards_considered')
    if (not isinstance(fresh, int) or isinstance(fresh, bool)
            or not isinstance(checked, int) or isinstance(checked, bool)
            or checked < 30 or fresh < 0):
        return default
    if fresh == 0:
        return ceiling
    if fresh <= 2:
        return min(ceiling, default+1)
    return default


def select_second_page_queries(queries, metric_history, page_limit=None):
    """Give scarce page-two requests to searches producing affordable cars.

    Only previous search-card observations are used. Never equate page counts,
    dealer stock, or prices above the buy budget with profitable opportunities.
    Keep exploratory pagination on other models to avoid selection lock-in.
    """
    page_limit = min(len(queries), page_limit if page_limit is not None
                     else (len(queries) + 2) // 3)
    if page_limit <= 0:
        return set()
    scores = []
    for index, query in enumerate(queries):
        observations = []
        for snapshot in metric_history[-7:] if isinstance(metric_history, list) else []:
            if not isinstance(snapshot, dict):
                continue
            record = snapshot.get(query)
            if not isinstance(record, dict) or record.get('http_status') != 200:
                continue
            if 'affordable_private_cards' not in record:
                continue
            try:
                affordable = max(0, int(record['affordable_private_cards']))
                unseen = max(0, int(record.get('unseen_affordable_private_cards') or 0))
                considered = max(1, int(record.get('unique_cards_seen') or 0))
            except (TypeError, ValueError, OverflowError):
                continue
            # An unseen affordable card is more useful than an already-checked one.
            observations.append((2 * unseen + affordable) / considered)
        if observations:
            baseline = sum(observations) / len(observations)
            # Compare actual unique cheap finds on page two, not just the
            # popularity of the same model on page one.
            page_two = []
            for snapshot in metric_history[-7:] if isinstance(metric_history, list) else []:
                record = snapshot.get(query) if isinstance(snapshot, dict) else None
                if not isinstance(record, dict) or record.get('http_status') != 200:
                    continue
                unique = record.get('second_page_unique_listing_cards')
                fresh = record.get('second_page_unseen_affordable_cards')
                if (not isinstance(unique, int) or isinstance(unique, bool)
                        or not isinstance(fresh, int) or isinstance(fresh, bool)
                        or unique < 0 or fresh < 0 or fresh > unique):
                    continue
                page_two.append(fresh / (unique + 5))
            score = (baseline * .20 + 4 * sum(page_two) / len(page_two)
                     if page_two else baseline)
            scores.append((score, index))
    # Use at most 70% exploitation and leave the rest to rotated exploration.
    exploit = min(len(scores), max(1, int(page_limit * .7)))
    chosen = {i for score, i in sorted(scores, key=lambda x: (-x[0], x[1]))[:exploit]
              if score > 0}
    # The normal spaced schedule remains the fallback until enough observed data exists.
    for i in range(0, len(queries), 3):
        if len(chosen) >= page_limit:
            break
        chosen.add(i)
    for i in range(len(queries)):
        if len(chosen) >= page_limit:
            break
        chosen.add(i)
    return chosen


def research_entry_price(row):
    """A research candidate needs a real, bounded price indication.

    Fixed asking prices and explicit minimum bids are different signals.
    Never turn ON_REQUEST, SEE_DESCRIPTION or a missing minimum bid into
    a fictitious zero-euro purchase price.
    """
    if not isinstance(row, dict):
        return None
    kind = row.get('price_type')
    if kind == 'FIXED':
        price = row.get('price')
    elif kind in ('MIN_BID', 'FAST_BID'):
        price = row.get('minimum_bid_eur')
    else:
        return None
    return price if finite_numeric_state(price) and 350 <= price <= MAX_BUY_PRICE else None


def comparison_search_plan(rows, previous, now, limit=4):
    """Search to complete specific valuations, bounded and with a six-hour query cooldown."""
    candidates = []
    history = previous.get('comparison_search_history') or {} if isinstance(previous, dict) else {}
    if not isinstance(history, dict):
        history = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or seller_evidence(row)['seller_type'] != 'particulier' or row.get('reserved'):
            continue
        if (row.get('severe_technical_risk') or over_purchase_mileage_limit(row)
                or listing_requires_manual_description_check(row)):
            continue
        try:
            checked = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
            if checked.tzinfo is None or not timedelta(0) <= now-checked <= timedelta(hours=24):
                continue
        except ValueError:
            continue
        facts = vehicle_facts(row)
        ident = market_identity(row)
        price = research_entry_price(row)
        if (price is None or not ident.get('model') or not ident.get('brand')
                or facts['year_detected'] is None or facts['mileage_km'] is None
                or facts['vehicle_data_warnings']):
            continue
        comparison = market_comparison(row)
        if comparison.get('status') != 'onvoldoende_vergelijkingen':
            continue
        query = ' '.join(str(v) for v in (ident['brand'], ident['model'], ident.get('engine_l')) if v)
        last_query = history.get(query)
        if (finite_numeric_state(last_query)
                and 0 <= now.timestamp() - last_query < 21600):
            continue
        try:
            sample = max(0, min(2, int(comparison.get('sample_size') or 0)))
        except (TypeError, ValueError, OverflowError):
            sample = 0
        # Complete near-finished valuations first. With the same progress,
        # prioritize fixed asking prices over bidding-floor research.
        candidates.append((3 - sample, row.get('price_type') != 'FIXED',
                           price, query, row))
    selected = {}
    for _, _, _, query, row in sorted(candidates, key=lambda x: x[:4]):
        selected.setdefault(query, row)
        if len(selected) >= limit:
            break
    return selected


def comparison_reference_targets(rows, now, limit=8):
    """Choose unfinished valuations for matching against already fetched search facts.

    Unlike dedicated comparison_search_plan queries, matching existing search
    results is not network work and must not inherit the six-hour query cooldown.
    """
    candidates = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or seller_evidence(row)['seller_type'] != 'particulier':
            continue
        if (row.get('reserved') or row.get('severe_technical_risk')
                or over_purchase_mileage_limit(row)
                or listing_requires_manual_description_check(row)):
            continue
        url = row.get('url')
        if not approved_listing_brand(url or ''):
            continue
        try:
            checked = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
            if checked.tzinfo is None or not timedelta(0) <= now-checked <= timedelta(hours=24):
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        ident, details = market_identity(row), vehicle_facts(row)
        price = research_entry_price(row)
        if (price is None or not ident.get('brand') or not ident.get('model')
                or details['year_detected'] is None or details['mileage_km'] is None
                or details['vehicle_data_warnings']):
            continue
        comparison = market_comparison(row)
        if comparison.get('status') != 'onvoldoende_vergelijkingen':
            continue
        try:
            sample = max(0, min(2, int(comparison.get('sample_size') or 0)))
        except (TypeError, ValueError, OverflowError):
            sample = 0
        needed = 3 - sample
        candidates.append((needed, row.get('price_type') != 'FIXED', price, url, row))
    candidates.sort(key=lambda x: x[:4])
    selected = {}
    for _, _, _, url, row in candidates:
        selected.setdefault(listing_id(url), row)
        if len(selected) >= limit:
            break
    return selected


def targeted_reference_links(facts, targets, history, cooldown, now_ts, limit=16,
                             cached_urls=None):
    groups = []
    for target in targets.values():
        ident, details = market_identity(target), vehicle_facts(target)
        options = []
        for url, row in facts.items():
            if (row.get('price_type') != 'FIXED' or row.get('reserved')
                    or not approved_listing_brand(url)
                    or obvious_nonrunning_search_card(row)):
                continue
            price = row.get('price')
            if not isinstance(price, (int, float)) or not 350 <= price <= 12000 or is_financing_offer(row):
                continue
            other, rd = market_identity(row), vehicle_facts(row)
            if (ident['model'] != other['model'] or ident.get('brand') != other.get('brand')
                    or any(ident[k] and other[k] and ident[k] != other[k] for k in ('engine_l', 'fuel', 'transmission', 'body'))
                    or rd['year_detected'] is None or rd['mileage_km'] is None or rd['vehicle_data_warnings']
                    or abs(rd['year_detected']-details['year_detected']) > 2
                    or abs(rd['mileage_km']-details['mileage_km']) > 50000
                    or listing_id(url) == listing_id(target.get('url'))):
                continue
            options.append((abs(rd['year_detected']-details['year_detected'])*20000+abs(rd['mileage_km']-details['mileage_km']), url))
        eligible = choose_detail_links(
            [u for _, u in sorted(options)], history, cooldown, now_ts,
            limit=4, cached_urls=cached_urls)
        groups.append(eligible)
    return interleave_search_results(groups)[:limit]


def fixed_reference_discovery_links(groups, facts, history, cooldown, now_ts,
                                    limit=12, cached_urls=None):
    """Reserve a small portion of detail checks for usable FIXED-price references.

    Otherwise a large supply of private bidding ads can fill the whole
    priority queue while the comparison archive lacks actual asking prices.
    Only public search records with vehicle facts and a fixed EUR price
    are eligible; these observations are not claimed as sale prices.
    """
    reference_groups = []
    priorities = {}
    for group in groups:
        choices = []
        for url in group:
            fact = facts.get(url) or {}
            price = fact.get('price')
            if (fact.get('price_type') != 'FIXED' or fact.get('reserved')
                    or obvious_nonrunning_search_card(fact)
                    or not isinstance(price, (int, float)) or isinstance(price, bool)
                    or not finite_numeric_state(price) or not 350 <= price <= 12000
                    or is_financing_offer(fact)):
                continue
            # Low-budget private fixed-price listings are purchase leads;
            # avoid using their scarce slots as generic reference research.
            if price <= MAX_BUY_PRICE and fact.get('seller_type') != 'handelaar':
                continue
            identity = market_identity(fact)
            vehicle = vehicle_facts(fact)
            if (not identity.get('model') or vehicle['year_detected'] is None
                    or vehicle['mileage_km'] is None or vehicle['vehicle_data_warnings']):
                continue
            choices.append(url)
            priorities[url] = (0, price)
        if choices:
            reference_groups.append(choices)
    return select_diverse_detail_links(reference_groups, history, cooldown, now_ts,
                                      limit=limit, cached_urls=cached_urls,
                                      priorities=priorities)


def shortlist_refresh_links(rows, history, cooldown, now, limit=8, cached_urls=None):
    """Refresh shortlisted ads only when the standard detail cooldown expires.

    Cached pages must wait 48h; other previously checked pages wait 6h.
    Price drops are handled independently, never by bypassing these limits.
    """
    candidates = []
    for row in rows if isinstance(rows, list) else []:
        if (not isinstance(row, dict) or not approved_listing_brand(row.get('url') or '')
                or over_purchase_mileage_limit(row)):
            continue
        try:
            checked = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
            if checked.tzinfo is None or now-checked < timedelta(hours=2):
                continue
        except ValueError:
            continue
        url = row['url']
        # Recent failed attempts also count; do not retry them every run.
        recent = {u: value for u, value in history.items() if listing_id(u) == listing_id(url)}
        try:
            if any(now-datetime.fromisoformat(str(v).replace('Z','+00:00')) < timedelta(hours=2) for v in recent.values()):
                continue
        except (ValueError, TypeError):
            continue
        if choose_detail_links([url], history, cooldown, now.timestamp(),
                               limit=1, cached_urls=cached_urls):
            candidates.append((checked, url))
    return list(dict.fromkeys(u for _, u in sorted(candidates)))[:limit]


def prune_superseded_search_reviews(previous, live_facts):
    """Discard carried card leads contradicted by this run's actual search JSON.

    A cached card cannot remain a potential fixed-price purchase after the same
    listing changes to bidding, becomes reserved, is clearly broken, or a
    dealer is identified. Missing live fields alone do not invalidate it.
    """
    previous = previous if isinstance(previous, list) else []
    live_facts = live_facts if isinstance(live_facts, dict) else {}
    invalid_ids = set()
    for url, fact in live_facts.items():
        if (not isinstance(url, str) or not isinstance(fact, dict)
                or not approved_listing_brand(url)):
            continue
        kind, price = fact.get('price_type'), fact.get('price')
        if (fact.get('reserved') or fact.get('seller_type') == 'handelaar'
                or over_purchase_mileage_limit(fact)
                or kind in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION')
                or obvious_nonrunning_search_card(fact)
                or is_financing_offer(fact)
                or (kind == 'FIXED' and isinstance(price, (int, float))
                    and not isinstance(price, bool) and finite_numeric_state(price)
                    and (price < 350 or price > MAX_BUY_PRICE))):
            invalid_ids.add(listing_id(url))
    kept = [row for row in previous if isinstance(row, dict)
            and listing_id(row.get('url') or '') not in invalid_ids]
    return kept, len(previous) - len(kept)


def prune_superseded_cached_reviews(previous, live_facts):
    """Never display yesterday's verified asking price over new public evidence.

    A search card is not a replacement for a detail-page verification. When
    its structured fixed asking price changes, hide the old cached review
    until a new detail check confirms the current terms. Absent/partial JSON
    is not proof that the previous listing disappeared.
    """
    rows, removed = prune_superseded_search_reviews(previous, live_facts)
    current_prices = {}
    for url, fact in (live_facts.items() if isinstance(live_facts, dict) else []):
        if (not isinstance(fact, dict) or not approved_listing_brand(url)
                or fact.get('price_type') != 'FIXED'):
            continue
        price = fact.get('price')
        if finite_numeric_state(price) and 350 <= price <= MAX_BUY_PRICE:
            current_prices[listing_id(url)] = price
    kept = []
    for row in rows:
        old = row.get('price')
        current = current_prices.get(listing_id(row.get('url')))
        if (row.get('price_type') == 'FIXED' and finite_numeric_state(old)
                and current is not None and abs(old - current) >= 1):
            removed += 1
            continue
        kept.append(row)
    return kept, removed


def retain_recent_search_card_reviews(current, previous, now, limit=250):
    """Keep unverified search-card leads during runs that skip fresh searches.

    Cards expire after 24 hours, remain explicitly unverified, and never
    substitute for a refreshed detail page or proof of availability.
    """
    retained = {}
    for rows, carried in ((previous, True), (current, False)):
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url = normalize_link(row.get('url'))
            price = row.get('price')
            if (not url or not approved_listing_brand(url)
                    or isinstance(price, bool) or not isinstance(price, (int, float))
                    or not finite_numeric_state(price) or not 350 <= price <= MAX_BUY_PRICE
                    or row.get('seller_type') == 'handelaar'
                    or row.get('price_type') in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION')
                    or row.get('reserved')
                    or obvious_nonrunning_search_card(row)
                    or over_purchase_mileage_limit(row)
                    or is_financing_offer(row)):
                continue
            try:
                discovered = datetime.fromisoformat(str(row.get('discovered_at')).replace('Z', '+00:00'))
                if discovered.tzinfo is None or not timedelta(0) <= now - discovered <= timedelta(hours=24):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            key = listing_id(url)
            old = retained.get(key)
            if old and str(old['discovered_at']) > discovered.isoformat():
                continue
            retained[key] = {**row, 'url': url, 'discovered_at': discovered.isoformat(),
                             'verified': False, 'manual_review_required': True,
                             'availability_check_required': True,
                             'not_rechecked_this_run': carried}
    def review_rank(row):
        # Fast screening only: prefer recent-year private-looking cards with
        # complete vehicle hints, not simply the oldest cheapest cars.
        year = row.get('year')
        private = row.get('seller_type') == 'particulier'
        return (row.get('seller_type') == 'handelaar',
                not private,
                not (isinstance(year, int) and year >= MIN_YEAR),
                row.get('mileage_km') is None,
                row['price'], row['discovered_at'], row['url'])
    return sorted(retained.values(), key=review_rank)[:limit]


def scan_health_label(run_blocked, search_errors, detail_errors,
                      search_requests, detail_requests):
    """Report whether actual public pages were checked, not just pending URLs."""
    if run_blocked:
        return 'blocked'
    if search_errors or detail_errors:
        return 'partial_errors'
    if search_requests or detail_requests:
        return 'ok'
    return 'idle_no_requests'


def ready_purchase_backlog(pending, last_checked, cooldown, now, cached_urls=None,
                           limit=60):
    """Count eligible purchase prospects, not dealer references or bid-only ads.

    The queue often contains hundreds of listings above the purchase budget.
    Those must not prevent searches for new, genuinely affordable cars. Use
    fixed-price facts or explicit *unverified* search-card price hints only.
    """
    candidates = []
    for url, row in pending.items():
        fact = row.get('facts') if isinstance(row, dict) else None
        if (not isinstance(fact, dict) or fact.get('reserved')
                or fact.get('seller_type') == 'handelaar'
                or obvious_nonrunning_search_card(fact)
                or over_purchase_mileage_limit(fact)):
            continue
        kind = fact.get('price_type')
        if kind not in (None, 'FIXED'):
            continue
        price = fact.get('price') if kind == 'FIXED' else None
        if price is None:
            price = fact.get('search_card_price_hint_eur')
        if (isinstance(price, bool) or not isinstance(price, (int, float))
                or not finite_numeric_state(price) or not 350 <= price <= MAX_BUY_PRICE):
            continue
        if is_financing_offer(fact):
            continue
        candidates.append(url)
    return choose_detail_links(candidates, last_checked, cooldown, now.timestamp(),
                               limit=limit, cached_urls=cached_urls)


def backlog_search_plan(queries, pending, last_checked, cooldown, now, previous,
                        cached_urls=None):
    affordable_ready = ready_purchase_backlog(
        pending, last_checked, cooldown, now, cached_urls=cached_urls, limit=40)
    # Even a rich backlog is not a reason to halt discovery completely.
    # Keep search volume bounded when the last successful scan is very recent.
    recent_success = False
    try:
        checked = datetime.fromisoformat(str(previous.get('checked_at')).replace('Z', '+00:00'))
        recent_success = (checked.tzinfo is not None
                          and timedelta(0) <= now - checked < timedelta(minutes=30)
                          and previous.get('scan_health') == 'ok'
                          and (previous.get('search_pages_requested') or 0) > 0)
    except (ValueError, TypeError, OverflowError):
        pass
    if len(affordable_ready) >= 40:
        return queries[::3][:6]
    if recent_success:
        utilization = previous.get('detail_budget_utilization_pct')
        if (isinstance(utilization, (int, float)) and not isinstance(utilization, bool)
                and finite_numeric_state(utilization) and utilization < 60
                and not previous.get('blocked_this_run')
                and not previous.get('search_errors')
                and not previous.get('detail_errors')):
            # Underfilled last run: keep two thirds of distinct model searches,
            # rather than repeating only half. Still bounded during rapid pushes.
            return [q for i, q in enumerate(queries) if i % 3 != 2]
        return queries[::2][:12]
    return queries


def fetch(url):
    # Never follow a persisted or generated link to an unapproved origin.
    try:
        target = urlparse(url)
        if target.scheme != 'https' or target.netloc not in ('www.marktplaats.nl', 'marktplaats.nl'):
            raise ValueError('Unapproved public-page origin')
    except (TypeError, AttributeError) as exc:
        raise ValueError('Invalid public-page URL') from exc
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=20) as response:
        html = response.read(1_500_000).decode('utf-8', 'replace')
        if re.search(r'<title[^>]*>\s*(?:Access Denied|Just a moment|Attention Required)', html, re.I):
            raise HTTPError(url, 403, 'Access challenge', response.headers, None)
        return html, response.geturl(), response.status

def parse(html):
    p = PageParser()
    p.feed(html)
    return p

def normalize_link(href):
    if not isinstance(href, str) or not href.strip():
        return None
    try:
        full = urljoin(BASE, href).split('#', 1)[0].split('?', 1)[0]
        u = urlparse(full)
    except ValueError:
        return None
    if u.scheme != 'https' or u.netloc not in ('www.marktplaats.nl', 'marktplaats.nl') or not u.path.startswith('/v/'):
        return None
    # Alleen echte auto-aanbiedingen, geen inkoopadvertenties of diensten.
    if not u.path.startswith('/v/auto-s/'):
        return None
    candidate = BASE + u.path.rstrip('/')
    return candidate if approved_listing_brand(candidate) else None

def ld_objects(page):
    def flatten(obj):
        if isinstance(obj, dict):
            yield obj
            for v in obj.values():
                yield from flatten(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from flatten(v)
    for script in page.ld:
        try:
            yield from flatten(json.loads(script))
        except (ValueError, TypeError):
            continue

def parse_price_eur(value):
    """Accept JSON numeric prices and Dutch thousands separators safely."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if finite_numeric_state(value) and value >= 0 else None
    raw = re.sub(r'(?i)^\s*(?:€|EUR)\s*', '', str(value).strip())
    raw = re.sub(r'[,.-][-–]$', '', raw)
    raw = re.sub(r'\s', '', raw)
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?', raw):
        raw = raw.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?', raw):
        raw = raw.replace(',', '')
    elif re.fullmatch(r'\d+(?:,\d{1,2})?', raw):
        raw = raw.replace(',', '.')
    elif not re.fullmatch(r'\d+(?:\.\d{1,2})?', raw):
        return None
    try:
        value = float(raw)
        return value if finite_numeric_state(value) else None
    except ValueError:
        return None


def clearly_over_budget_search_card(text, max_price=12000):
    """Skip only unambiguously overpriced search cards; keep uncertain ones."""
    if not text or re.search(r'(?i)per maand|maand|lease|p/m', text):
        return False
    amounts = re.findall(r'(?:€\s*|\bEUR\s+)([0-9][0-9.,]*)', text, flags=re.I)
    if len(amounts) != 1:
        return False
    amount = parse_price_eur(amounts[0])
    return amount is not None and amount > max_price


def search_card_signals(text):
    """Conservative hints only, never a verified vehicle valuation."""
    text = ' '.join(str(text or '').split())
    price_matches = list(re.finditer(r'(?:€\s*|\bEUR\s+)([0-9][0-9.,]*)', text, flags=re.I))
    parsed_amounts = []
    for match in price_matches:
        before = text[max(0, match.start()-40):match.start()].lower()
        after = text[match.end():match.end()+40].lower().lstrip(' ,.-–')
        if (re.search(r'(?:wegenbelasting|motorrijtuigenbelasting|mrb|aanbetaling|maandbedrag|termijnbedrag|lease|huurkoop|vanaf)\s*:?\s*$', before)
                or re.match(r'(?:p/m|per (?:maand|week|kwartaal|jaar)|/\s*(?:mnd|maand|week)|aanbetaling)\b', after)
                or re.match(r'(?:wegenbelasting|mrb)\s*(?:$|[.;,])', after)):
            continue
        parsed_amounts.append(parse_price_eur(match.group(1).rstrip('.,')))
    # Repeated identical prices are safe; conflicting prices are not.
    distinct_prices = {p for p in parsed_amounts if p is not None}
    price = next(iter(distinct_prices)) if len(distinct_prices) == 1 else None
    # Identify payment context for the exact amount, not generic financing copy.
    price_matches = list(re.finditer(r'(?:€\s*|\bEUR\s+)([0-9][0-9.,]*)', text, flags=re.I))
    if price is not None:
        for match in price_matches:
            if parse_price_eur(match.group(1).rstrip('.,')) != price:
                continue
            before = text[max(0, match.start()-30):match.start()].lower()
            after = text[match.end():match.end()+40].lower().lstrip(' ,.-–')
            monthly_after = re.match(r'(?:/\s*(?:mnd|maand)|per maand|p/m|p\.?mnd|per week|/week)\b', after)
            payment_before = re.search(r'(?:huurkoop|lease|termijnbedrag|maandbedrag|aanbetaling|vanaf)\s*$', before)
            payment_after = re.match(r'(?:termijnbedrag|maandbedrag|huurkoop|aanbetaling)\b', after)
            if monthly_after or payment_before or payment_after:
                price = None
                break
    # APK expiry dates are not vehicle model years. Search cards often
    # contain both "bouwjaar 2009" and "APK 02-2027".
    explicit_years = [int(m.group(1)) for m in re.finditer(
        r'(?i)\b(?:bouwjaar|bj\.?|uit)\s*[:=-]?\s*((?:19[89]|20[0-2])\d)\b', text)]
    years = []
    for match in re.finditer(r'(?<!\d)(?:19[89]\d|20[0-2]\d)(?!\d)', text):
        value = int(match.group())
        if not 1990 <= value <= datetime.now(timezone.utc).year + 1:
            continue
        preceding = text[max(0, match.start()-32):match.start()]
        if re.search(r'(?i)\bapk\b[^€.!?]{0,27}$', preceding):
            continue
        years.append(value)
    if len(set(explicit_years)) == 1:
        years = explicit_years
    km_values = []
    for match in re.finditer(r'(?i)(\d{1,3}(?:[.,]\d{3})+|\d{4,6})\s*(?:km|kilometer)', text):
        try:
            km = int(re.sub(r'\D', '', match.group(1)))
            if 1000 <= km <= 700000:
                km_values.append(km)
        except ValueError:
            pass
    return {'price': price, 'year': years[0] if len(set(years)) == 1 else None,
            'mileage_km': km_values[0] if len(set(km_values)) == 1 else None}


def search_structured_prices(page):
    """Map listing URLs to explicit prices in search-page JSON-LD, if provided."""
    prices = {}
    conflicts = set()
    for obj in ld_objects(page):
        url = obj.get('url') or obj.get('@id')
        if not isinstance(url, str):
            continue
        link = normalize_link(url)
        if not link or not approved_listing_brand(link):
            continue
        offers = obj.get('offers')
        offers = offers if isinstance(offers, list) else [offers]
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            amount = parse_price_eur(offer.get('price'))
            currency = offer.get('priceCurrency')
            if amount is None or not 350 <= amount <= 12000:
                continue
            if currency and str(currency).upper() != 'EUR':
                continue
            if link in prices and prices[link] != amount:
                conflicts.add(link)
            prices[link] = amount
    for link in conflicts:
        prices.pop(link, None)
    return prices


def search_card_priority(text):
    """Prefer plausible affordable ads, without excluding unknown or older cars."""
    hints = search_card_signals(text)
    price = hints['price']
    if price is None:
        return (2, 0)
    if 350 <= price <= 2000:
        return (0, abs(price - 1100))
    if 2000 < price <= MAX_BUY_PRICE:
        return (1, price)
    # Cars above the actual buy budget should not consume detail slots
    # ahead of unknown-price candidates; still allow market comparables.
    return (3, price)


def extract_fields(page, listing_url=None):
    result = {'title': page.meta.get('og:title'), 'description': None, 'description_source': 'ontbreekt', 'price': None,
              'currency': None, 'year': None, 'mileage': None, 'location': None, 'seller_name': None, 'seller_type': None}
    # Bind every field to one vehicle; recommendations must never overwrite it.
    vehicles = []
    for obj in ld_objects(page):
        types = obj.get('@type', [])
        types = types if isinstance(types, list) else [types]
        if any(str(t).lower() in ('product', 'vehicle', 'car') for t in types):
            vehicles.append(obj)
    target = normalize_link(listing_url or page.meta.get('og:url') or '')
    def object_url(o):
        offers = o.get('offers')
        offer_url = offers.get('url') if isinstance(offers, dict) else None
        return o.get('url') or o.get('@id') or offer_url or ''
    exact = [o for o in vehicles if target and
             normalize_link(str(object_url(o))) == target]
    candidates = exact or [o for o in vehicles if not object_url(o)]
    if not target:
        candidates = vehicles
    obj = candidates[0] if len(candidates) == 1 else {}
    if not result['title'] and obj.get('name'):
        result['title'] = str(obj['name'])
    description = obj.get('description')
    if isinstance(description, str) and len(description.strip()) >= 10:
        result['description'] = description.strip()[:12000]
        result['description_source'] = 'jsonld'
    offers = obj.get('offers', [])
    offers = offers if isinstance(offers, list) else [offers]
    offers = [o for o in offers if isinstance(o, dict)]
    prices = {(parse_price_eur(o.get('price')), str(o.get('priceCurrency') or 'EUR').upper())
              for o in offers}
    if len(prices) == 1:
        amount, currency = next(iter(prices))
        if amount is not None and currency == 'EUR':
            result['price'], result['currency'] = amount, currency
            result['price_source'] = 'jsonld'
    for seller in [obj.get('seller')] + [o.get('seller') for o in offers]:
        if isinstance(seller, dict):
            result['seller_name'] = result['seller_name'] or seller.get('name')
            kind = str(seller.get('@type') or '').lower()
            if kind in ('organization', 'localbusiness', 'autodealer'):
                result['seller_type'] = 'business'
            elif kind == 'person' and result['seller_type'] != 'business':
                result['seller_type'] = 'unknown'
    mileage = obj.get('mileageFromOdometer')
    if isinstance(mileage, dict):
        unit = str(mileage.get('unitCode') or mileage.get('unitText') or 'KMT').lower()
        if unit in ('kmt', 'km', 'kilometer', 'kilometre'):
            result['mileage'] = mileage.get('value')
    result['year'] = obj.get('vehicleModelDate')
    address = obj.get('address')
    if isinstance(address, dict):
        result['location'] = address.get('addressLocality')
    descriptions = page.detail_blocks.get('description', [])
    if len(descriptions) == 1 and obj:
        result['description'] = descriptions[0][:20000]
        result['description_source'] = 'pagina_tekst'
        result['description_complete'] = len(descriptions[0]) <= 20000
    locations = page.detail_blocks.get('location', [])
    if len(set(locations)) == 1:
        result['location'] = locations[0]
    result['apk_text'] = ' '.join(page.detail_blocks.get('apk', []))
    result['fuel_type'] = (obj.get('vehicleEngine') or {}).get('fuelType') if isinstance(obj.get('vehicleEngine'), dict) else None
    result['transmission'] = obj.get('vehicleTransmission')
    result['body_type'] = obj.get('bodyType')
    result['extraction_version'] = 3
    expected = re.search(r'/([ma]\d+)(?:-|$)', str(listing_url or page.meta.get('og:url') or ''))
    config = public_listing_config(page.scripts, expected.group(1) if expected else None)
    result['image_urls'] = photo_urls(obj.get('image') or [page.meta.get('og:image')])
    if config:
        result['title'] = result['title'] or config.get('title')
        if len(descriptions) == 1:
            result['description'] = descriptions[0][:20000]
            result['description_source'] = 'pagina_tekst'
            result['description_complete'] = len(descriptions[0]) <= 20000
        seller = config.get('seller')
        seller = seller if isinstance(seller, dict) else {}
        location = seller.get('location')
        location = location if isinstance(location, dict) else {}
        result['seller_name'] = seller.get('name') or result['seller_name']
        result['seller_platform_type'] = seller.get('sellerType')
        result['location'] = location.get('cityName') or result['location']
        result['reserved'] = bool(config.get('isReserved'))
        result['unavailable'] = any(config.get(key) is True for key in ('isSold', 'isExpired', 'isDeleted'))
        info = config.get('priceInfo')
        valid_price_info = isinstance(info, dict)
        info = info if valid_price_info else {}
        result['price_type'] = info.get('priceType')
        cents = price_from_cents(info.get('priceCents'))
        if not valid_price_info or not result['price_type']:
            # JSON-LD amounts without a valid platform price type might be a
            # bid floor or price-on-request; never assert a fixed asking price.
            result['price'] = None
            result['price_source'] = None
        if result['price_type'] == 'FIXED':
            if cents is not None:
                result['price'] = cents
                result['price_source'] = 'platform_priceInfo'
            elif result['price'] is not None:
                result['price_source'] = 'jsonld_fallback_fixed'
        elif result['price_type']:
            result['price_source'] = None
            result['minimum_bid_eur'] = cents if result['price_type'] == 'MIN_BID' else None
            result['price'] = None
        gallery = config.get('gallery')
        gallery = gallery if isinstance(gallery, dict) else {}
        result['image_urls'] = photo_urls(gallery.get('imageUrls', [])) or result['image_urls']
        car_attributes = config.get('carAttributes')
        car_attributes = car_attributes if isinstance(car_attributes, dict) else {}
        groups = car_attributes.get('groupedWithIcons')
        groups = groups if isinstance(groups, list) else []
        attrs = {}
        for group in groups:
            values = group.get('attributes', []) if isinstance(group, dict) else []
            for attribute in values if isinstance(values, list) else []:
                if isinstance(attribute, dict) and isinstance(attribute.get('key'), str):
                    attrs[attribute['key']] = attribute.get('value')
        result['fuel_type'] = attrs.get('fuel') or result['fuel_type']
        result['transmission'] = attrs.get('transmission') or result['transmission']
        result['body_type'] = attrs.get('vehicleType') or result['body_type']
        result['engine_l'] = str(attrs.get('cylinderCapacity') or '') or None
        result['vehicle_data_conflicts'] = []
        structured_km, config_km = parse_mileage(result['mileage']), parse_mileage(attrs.get('mileage'))
        if structured_km is not None and config_km is not None and abs(structured_km - config_km) > 1000:
            result['vehicle_data_conflicts'].append('Tegenstrijdige kilometerstanden tussen advertentiebronnen')
        if result['year'] and attrs.get('constructionYear') and str(result['year']) != str(attrs['constructionYear']):
            result['vehicle_data_conflicts'].append('Tegenstrijdige bouwjaren tussen advertentiebronnen')
        result['year'] = attrs.get('constructionYear') or result['year']
        result['mileage'] = attrs.get('mileage') or result['mileage']
    if any(str(o.get('availability') or '').rsplit('/', 1)[-1] in ('SoldOut', 'Discontinued') for o in offers):
        result['unavailable'] = True
    result.setdefault('price_source', None)
    result['price_missing_reason'] = (None if result['price'] is not None else
        'bieden_geen_vraagprijs' if result.get('price_type') in ('MIN_BID', 'FAST_BID') else
        'prijs_op_aanvraag_of_in_tekst' if result.get('price_type') in ('ON_REQUEST', 'SEE_DESCRIPTION') else
        'geen_eenduidige_verkoopprijs')
    result['image_url'] = result['image_urls'][0] if result['image_urls'] else None
    result['photo_count'] = len(result['image_urls'])
    # A schema Person is not proof of a private seller; business copy is stronger evidence.
    if re.search(r'(?im)^\s*(?:autobedrijf|autohandel|garagebedrijf)\s+\S', result.get('description') or ''):
        result['seller_type'] = 'business'
    if not result['description']:
        fallback = page.meta.get('og:description') or page.meta.get('description')
        if isinstance(fallback, str) and fallback.strip():
            result['description'] = fallback.strip()[:12000]
            result['description_source'] = 'meta_samenvatting'
    result.update(seller_evidence(result))
    if result['seller_type'] == 'handelaar':
        result['seller_type'] = 'business'
    return result

def listing_requires_manual_description_check(item):
    """Short summaries cannot establish that an engine is healthy."""
    description = str(item.get('description') or '').strip()
    # A long JSON-LD text is still an unverified seller claim, but short
    # snippets must never be treated as a complete mechanical inspection.
    return item.get('description_source') not in ('jsonld', 'pagina_tekst') or len(description) < 180


def is_financing_offer(item):
    """Reject instalment adverts, not legitimate cars with financing mentioned."""
    title = str(item.get('title') or '').lower()
    description = str(item.get('description') or '').lower()
    price = item.get('price')
    if any(term in title for term in ('financial lease', 'private lease', 'per maand', 'p/m', 'maandbedrag')):
        return True
    # Match complete amounts, including Dutch decimals and the usual ,- suffix.
    # A price of 199 must never match the tail of 1199.
    if isinstance(price, (int, float)):
        for match in re.finditer(r'(?<![\d.,])(?:€\s*)?(\d[\d.,]*)(?:,-)?\s*', description):
            amount = parse_price_eur(match.group(1).rstrip('.,'))
            after = description[match.end():].lstrip(' ,.-–')
            before = description[max(0, match.start()-30):match.start()]
            if amount == price and re.search(r'(?:aanbetaling|maandbedrag|termijnbedrag)\s*:?\s*$', before):
                return True
            if amount == price and re.match(r'(?:p/m|per maand|/mnd|/maand|per week|/week)\b', after):
                return True
    return False


def model_review_price(item):
    """User-calibrated interesting *asking* prices; inspect year, km and condition."""
    title = str(item.get('title') or '').lower()
    for model, ceiling in MODEL_REVIEW_PRICES.items():
        if model in title:
            return ceiling
    return None


def buying_opportunity(item):
    """Explain why a car deserves a phone call; never promise resale profit."""
    if over_purchase_mileage_limit(item):
        return {'buyer_queue': 'uitsluiten',
                'buyer_reason': f'Kilometerstand boven inkoopgrens van {MAX_PURCHASE_MILEAGE_KM:,} km'.replace(',', '.')}
    if item.get('vehicle_data_warnings'):
        return {'buyer_queue': 'verder_onderzoek',
                'buyer_reason': 'Tegenstrijdige voertuiggegevens; kilometerstand en bouwjaar eerst controleren'}
    price = item.get('price')
    if item.get('price_type') in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION'):
        return {'buyer_queue': 'prijs_opvragen', 'buyer_reason': 'Vraag eerst de verkoopprijs; geen vaste koopprijs vastgesteld'}
    if not isinstance(price, (int, float)) or price > MAX_BUY_PRICE:
        return {'buyer_queue': 'buiten_budget', 'buyer_reason': 'Boven inkoopbudget'}
    if is_financing_offer(item):
        return {'buyer_queue': 'uitsluiten', 'buyer_reason': 'Maandtermijn of leaseaanbieding'}
    if item.get('severe_technical_risk'):
        return {'buyer_queue': 'reparatieproject', 'buyer_reason': 'Ernstig technisch risico; niet als normale occasion beoordelen'}
    max_bid = item.get('max_purchase_price_eur')
    if isinstance(max_bid, (int, float)):
        if price <= max_bid:
            return {'buyer_queue': 'prijscheck_bellen',
                    'buyer_reason': f'Vraagprijs binnen conservatieve maximale inkoopprijs voor EUR {minimum_profit():g} marge vóór belasting; inspectie vereist'}
        discount = (price - max_bid) / price if price else 1
        if discount > 0.30:
            return {'buyer_queue': 'grote_prijskloof',
                    'buyer_reason': f'Minimaal {round(discount * 100)}% korting nodig voor EUR {minimum_profit():g} marge vóór belasting; niet als kansrijke deal rangschikken'}
        return {'buyer_queue': 'onderhandelen_tot_maxbod',
                'buyer_reason': f'Alleen interessant na minimaal EUR {round(price - max_bid)} korting; maximale biedprijs EUR {round(max_bid)}'}
    tier = item.get('watchlist_tier')
    if tier in ('sterke_prijsafwijking_controleren', 'prijsafwijking_controleren'):
        return {'buyer_queue': 'prijscheck_bellen', 'buyer_reason': 'Vraagprijs ligt onder vergelijkbare advertenties; technische staat verifiëren'}
    if tier == 'goedkope_oudere_auto_controleren':
        return {'buyer_queue': 'goedkoop_onderhandelen', 'buyer_reason': 'Goedkope oudere auto; marge pas na inspectie en onderhandeling'}
    if price <= 1250 and item.get('seller_type') != 'handelaar':
        return {'buyer_queue': 'goedkoop_onderhandelen', 'buyer_reason': 'Lage vraagprijs; verkoper en technische staat controleren, onderhandelen mogelijk'}
    if price <= 1750 and item.get('seller_type') == 'particulier':
        return {'buyer_queue': 'goedkoop_onderhandelen', 'buyer_reason': 'Betaalbare particuliere occasion; vraagprijs en onderhoud controleren'}
    return {'buyer_queue': 'verder_onderzoek', 'buyer_reason': 'Nog onvoldoende bewijs voor interessante inkoopprijs'}


def deal_watchlist(item, rating, comparison):
    if over_purchase_mileage_limit(item):
        return {'watchlist_tier': 'uitsluiten',
                'watchlist_reason': f'Meer dan {MAX_PURCHASE_MILEAGE_KM:,} km'.replace(',', '.')}
    if item.get('price_type') in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION') and not rating.get('severe_technical_risk'):
        return {'watchlist_tier': 'handmatig_prijsonderzoek', 'watchlist_reason': 'Verkoopprijs nog opvragen; biedlimiet alleen bij voldoende vergelijkingen'}
    return base_deal_watchlist(item, rating, comparison)


def unsafe_for_purchase_shortlist(row):
    """Never recommend a negotiation before essential mechanical/data checks.

    Also inspect persisted old records: a stale bid list must not resurrect
    a car with a known defect after the selection rules are updated.
    """
    from marktplaats_market import technical_risk_signals, extra_repair_reserve
    from car_triage import HIGH_RISK
    if not isinstance(row, dict):
        return True
    text = (str(row.get('title') or '') + ' ' + str(row.get('description') or '')).lower()
    # Legacy persisted leads may carry severe_technical_risk=False despite
    # explicitly describing a non-running engine or other serious fault.
    if any(term in text for term in HIGH_RISK):
        return True
    if (row.get('reserved') or row.get('unavailable')
            or row.get('severe_technical_risk')
            or row.get('repair_review_required')
            or row.get('apk_status') in ('ongunstig', 'verloopt_binnen_30_dagen')
            or over_purchase_mileage_limit(row)
            or is_financing_offer(row)):
        return True
    try:
        return bool(vehicle_facts(row).get('vehicle_data_warnings')
                    or technical_risk_signals(row) or extra_repair_reserve(row)[0])
    except (TypeError, ValueError, AttributeError, OverflowError):
        return True


def purchase_review_tier(row):
    """Rank for manual inspection without claiming a verified profit."""
    year = row.get('year_detected') or row.get('year')
    if isinstance(year, str):
        year = int(year.strip()) if re.fullmatch(r'(?:19|20)\d{2}', year.strip()) else None
    price = row.get('price')
    if row.get('severe_technical_risk') or row.get('apk_status') == 'ongunstig':
        return 'technisch_risico'
    if over_purchase_mileage_limit(row):
        return 'kilometerstand_boven_grens'
    if row.get('price_type') in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION'):
        return 'handmatig_beoordelen'
    if not isinstance(price, (int, float)) or price > MAX_BUY_PRICE:
        return 'buiten_budget'
    if row.get('seller_type') == 'handelaar' and not row.get('exceptional_dealer_deal'):
        return 'handelaar_lage_prioriteit'
    if year is None:
        return 'bouwjaar_controleren'
    if row.get('seller_type') == 'particulier':
        return 'particulier_eerst_bekijken'
    return 'handmatig_beoordelen'


def extract_vehicle_year(raw_year, title):
    """Avoid treating APK expiry dates as a vehicle's production year."""
    if raw_year is not None:
        match = re.search(r'\b(?:19|20)\d{2}\b', str(raw_year))
        if match:
            return int(match.group())
    clean = re.sub(r'(?i)\bapk\b[^,;|\n]{0,45}', ' ', str(title or ''))
    match = re.search(r'\b(?:19|20)\d{2}\b', clean)
    return int(match.group()) if match else None


def year_hint_new(title):
    return bool(re.search(r'\b20(1[8-9]|2[0-6])\b', title))

def choose_detail_links(found, history, cooldown, now_ts, limit=MAX_PER_RUN,
                        cached_urls=None):
    """Prefer unseen ads; reuse cached market observations rather than re-fetching.

    Already archived pages refresh after 48 hours. Other checked pages
    refresh after 6 hours; both remain subject to the detail request cap. A 403/429 still ends the current run.
    """
    cached_urls = cached_urls or set()
    cached_ids = {listing_id(url) for url in cached_urls}
    checked_by_id = {}
    for url, value in history.items():
        key = listing_id(url)
        checked_by_id[key] = max(str(value), checked_by_id.get(key, ''))
    cooldown_by_id = {}
    for url, value in cooldown.items():
        key = listing_id(url)
        cooldown_by_id[key] = max(value, cooldown_by_id.get(key, 0))
    fresh, revisit = [], []
    seen_ids = set()
    cutoff = datetime.fromtimestamp(now_ts, timezone.utc)
    for url in found:
        key = listing_id(url)
        if key in seen_ids or cooldown_by_id.get(key, 0) > now_ts:
            continue
        seen_ids.add(key)
        last = checked_by_id.get(key)
        if not last:
            fresh.append(url)
            continue
        try:
            age = datetime.fromisoformat(str(last).replace('Z', '+00:00'))
            if age.tzinfo is None:
                continue
            interval = timedelta(hours=48) if key in cached_ids else timedelta(hours=6)
            if age > cutoff - interval:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        revisit.append((age, url))
    revisit.sort(key=lambda entry: entry[0])
    return (fresh + [url for _, url in revisit])[:limit]


def select_diverse_detail_links(groups, history, cooldown, now_ts, limit=MAX_PER_RUN,
                                cached_urls=None, priorities=None, facts=None):
    """Select fresh, promising listings across all brands, with fair coverage.

    Unknown-price ads remain eligible; no price hint is treated as a valuation.
    """
    priorities = priorities or {}
    facts = facts or {}
    available = [choose_detail_links(group, history, cooldown, now_ts,
                 limit=len(group), cached_urls=cached_urls) for group in groups]
    checked_ids = {listing_id(url) for url in history}
    def rank(url):
        # Within the same priority tier, prefer never-checked listings.
        # Scores can contain extra tie-breakers in the refill queue.
        # A known price drop still gets an explicit refresh slot separately.
        score = tuple(priorities.get(url, (2, 0)))
        # First separate genuine purchase prospects from bidding research and
        # dealer/reference stock. Within a tier, examine *new* cars before
        # repeatedly checking an older, already assessed private listing.
        # Explicit detected price drops are independently scheduled above.
        tier = score[0]
        bucket = 0 if tier < 0 else 1 if tier <= 2 else 2
        year_rank = mileage_rank = 0
        if bucket == 0 and isinstance(facts.get(url), dict):
            year, km, warnings = search_card_vehicle_hints(facts[url])
            year_rank = (0 if isinstance(year, int) and year >= MIN_YEAR else
                         1 if year is None else 2)
            mileage_rank = (0 if isinstance(km, (int, float)) and km <= 250000 else
                            1 if km is None else 2)
            if warnings:
                mileage_rank = 3
        # Better-documented affordable cars rank earlier, but old cars remain.
        return (bucket, listing_id(url) in checked_ids,
                year_rank, mileage_rank, tier, *score[1:])
    selected, used = [], set()
    # One candidate per search group, ordered by the strongest visible hint.
    heads = [(rank(best), index, best)
             for index, group in enumerate(available) if group
             for best in [min(group, key=rank)]]
    for _, _, url in sorted(heads):
        if len(selected) >= limit:
            return selected
        if listing_id(url) not in used:
            selected.append(url)
            used.add(listing_id(url))
    # Then spend remaining requests on the best hints across all groups.
    # Include group position as a tie-breaker for brand diversity.
    remaining = [(rank(url), pos, group_index, url)
                 for group_index, group in enumerate(available)
                 for pos, url in enumerate(group) if listing_id(url) not in used]
    for _, _, _, url in sorted(remaining):
        if len(selected) >= limit:
            break
        if listing_id(url) not in used:
            selected.append(url)
            used.add(listing_id(url))
    return selected


def affordable_unverified_search_price(fact):
    """A visible price hint can prioritize a detail check, never prove its price.

    A public FIXED-price listing sometimes has no priceCents in search JSON.
    It must not be demoted to generic unknown research when the same listing
    card displays a plausible amount. Bid/request types and conflicts between
    two populated prices remain excluded.
    """
    if not isinstance(fact, dict) or fact.get('price_type') not in (None, 'FIXED'):
        return False
    if (fact.get('price') is not None or fact.get('reserved')
            or fact.get('seller_type') == 'handelaar'
            or obvious_nonrunning_search_card(fact)
            or is_financing_offer(fact)):
        return False
    amount = fact.get('search_card_price_hint_eur')
    return (isinstance(amount, (int, float)) and not isinstance(amount, bool)
            and finite_numeric_state(amount) and 350 <= amount <= MAX_BUY_PRICE)


def refill_underused_detail_budget(selected, groups, history, cooldown, now_ts,
                                   limit, facts=None, priorities=None, cached_urls=None,
                                   unknown_limit=12):
    """Fill slots released when initial broad discovery is filtered.

    The initial diverse selection intentionally caps dealer/reference and
    expensive listings. That cap must not discard the rest of the eligible
    private-price queue: otherwise we make e.g. 50 of 100 allowed checks while
    many fresh purchase candidates remain. Respect all existing cooldowns and
    never treat bid floors as fixed asking prices.
    """
    selected = list(selected)[:limit]
    if len(selected) >= limit:
        return selected
    facts = facts or {}
    priorities = priorities or {}
    existing = {listing_id(url) for url in selected}
    eligible_groups = []
    rank = {}
    for group in groups:
        candidates = []
        for url in group:
            if listing_id(url) in existing:
                continue
            fact = facts.get(url) or {}
            if (fact.get('reserved') or fact.get('seller_type') == 'handelaar'
                    or obvious_nonrunning_search_card(fact)
                    or over_purchase_mileage_limit(fact)):
                continue
            kind = fact.get('price_type')
            amount = fact.get('price')
            if kind == 'FIXED':
                if (isinstance(amount, (int, float)) and
                        (isinstance(amount, bool) or not finite_numeric_state(amount) or
                         amount > MAX_BUY_PRICE or amount < 350)):
                    continue
                tier = 0 if (isinstance(amount, (int, float)) or
                             affordable_unverified_search_price(fact)) else 2
            elif kind in ('MIN_BID', 'FAST_BID'):
                floor = fact.get('minimum_bid_eur')
                if isinstance(floor, (int, float)) and floor > MAX_BUY_PRICE:
                    continue
                tier = 1
            elif kind in ('ON_REQUEST', 'SEE_DESCRIPTION'):
                tier = 2
            elif kind:
                tier = 2
            else:
                # Search-card hints can identify fixed prices even without
                # structured fields; their price remains unverified.
                hint = priorities.get(url, (2, 0))
                tier = 0 if affordable_unverified_search_price(fact) or hint[0] < 0 else 2
            if tier == 0 and is_financing_offer(fact):
                continue
            hint = priorities.get(url, (2, 0))
            rank[url] = (tier, hint[0], hint[1])
            candidates.append(url)
        if candidates:
            eligible_groups.append(candidates)
    if not eligible_groups:
        return selected
    # Ask for all currently eligible alternatives, then fill remaining slots.
    # The helper still respects full history, global per-URL cooldown, and
    # cached 48-hour vs uncached 6-hour revisit intervals.
    possible = select_diverse_detail_links(
        eligible_groups, history, cooldown, now_ts,
        limit=sum(map(len, eligible_groups)), cached_urls=cached_urls,
        priorities=rank, facts=facts)
    unknown_added = 0
    for url in possible:
        if len(selected) >= limit:
            break
        key = listing_id(url)
        if key in existing:
            continue
        if rank[url][0] == 2:
            if unknown_added >= unknown_limit:
                continue
            unknown_added += 1
        existing.add(key)
        selected.append(url)
    return selected


def detail_research_category(url, facts):
    """Purchase intent and research intent must never share a price assumption."""
    row = facts.get(url) if isinstance(facts, dict) else None
    fact = row if isinstance(row, dict) else {}
    if fact.get('reserved') or obvious_nonrunning_search_card(fact):
        return 'clear_risk'
    if over_purchase_mileage_limit(fact):
        return 'reference'
    kind = fact.get('price_type')
    price = fact.get('price')
    if fact.get('seller_type') == 'handelaar':
        return 'reference'
    if kind in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION'):
        return 'bid_or_request'
    if (kind == 'FIXED' and isinstance(price, (int, float))
            and not isinstance(price, bool) and finite_numeric_state(price)
            and 350 <= price <= MAX_BUY_PRICE and not is_financing_offer(fact)):
        return 'affordable_fixed'
    if affordable_unverified_search_price(fact):
        return 'unverified_price_hint'
    if kind == 'FIXED' and isinstance(price, (int, float)):
        return 'reference'
    return 'unknown'


def bound_detail_research_mix(selected, facts, limit, bid_limit=24,
                              reference_limit=35, unknown_limit=14):
    """Spend scarce detail checks on purchase stock before reference research.

    Bid floors remain research-only. Hard limits prevent hundreds of existing
    bid/dealer cards crowding out fresh private fixed-price advertisements.
    Does not fetch extra URLs or override any cooldown.
    """
    caps = {'bid_or_request': bid_limit, 'reference': reference_limit,
            'unknown': unknown_limit, 'clear_risk': 0}
    counts = {name: 0 for name in
              ('affordable_fixed', 'unverified_price_hint', 'bid_or_request',
               'reference', 'unknown', 'clear_risk')}
    used = set()
    selected = list(selected)
    prioritized = (
        [u for u in selected if detail_research_category(u, facts) in
         ('affordable_fixed', 'unverified_price_hint')]
        + [u for u in selected if detail_research_category(u, facts) not in
           ('affordable_fixed', 'unverified_price_hint')]
    )
    result = []
    for url in prioritized:
        key = listing_id(url)
        kind = detail_research_category(url, facts)
        if key in used or len(result) >= limit:
            continue
        if kind in caps and counts[kind] >= caps[kind]:
            continue
        counts[kind] += 1
        result.append(url)
        used.add(key)
    counts['total_selected'] = len(result)
    counts['selection_limit'] = limit
    counts['rejected_by_mix_caps'] = max(0, len({listing_id(u) for u in selected}) - len(result))
    return result, counts


def refill_after_research_caps(selected, groups, facts, history, cooldown, now_ts,
                               limit, cached_urls=None, target_models=None, max_added=8):
    """Reuse at most eight detail slots lost to the final research-mix caps.

    The previous refill happened BEFORE excluding excess bids and weak
    references. A full provisional selection could shrink from 107 to 80
    requests, leaving useful fixed-price research untouched. This helper
    operates entirely on known URLs and respects the original eligibility,
    fixed category caps, pacing, and total request budget.
    """
    selected = list(selected)[:limit]
    max_added = max(0, min(8, max_added, limit-len(selected)))
    if max_added == 0:
        return selected, 0
    facts = facts if isinstance(facts, dict) else {}
    target_models = set(target_models or ())
    caps = {'bid_or_request': 24, 'reference': 35, 'unknown': 14, 'clear_risk': 0}
    counts = {}
    used = {listing_id(url) for url in selected}
    for url in selected:
        kind = detail_research_category(url, facts)
        counts[kind] = counts.get(kind, 0) + 1
    # All of these are *local* selections; the helper never fetches pages.
    eligible = select_diverse_detail_links(
        groups, history, cooldown, now_ts,
        limit=sum(len(group) for group in groups), cached_urls=cached_urls,
        facts=facts)
    ranked = []
    for pos, url in enumerate(eligible):
        if listing_id(url) in used or not approved_listing_brand(url):
            continue
        fact = facts.get(url)
        if not isinstance(fact, dict) or fact.get('reserved'):
            continue
        kind = detail_research_category(url, facts)
        if kind in ('affordable_fixed', 'unverified_price_hint'):
            if unsafe_for_purchase_shortlist(fact):
                continue
            rank = 0 if kind == 'affordable_fixed' else 1
        elif kind == 'reference' and fact.get('price_type') == 'FIXED':
            price = fact.get('price')
            if (not finite_numeric_state(price) or not 350 <= price <= 12000
                    or obvious_nonrunning_search_card(fact)
                    or is_financing_offer(fact)):
                continue
            from marktplaats_market import technical_risk_signals, extra_repair_reserve
            if technical_risk_signals(fact) or extra_repair_reserve(fact)[0]:
                continue
            try:
                vehicle = vehicle_facts(fact)
                ident = market_identity(fact)
                if (vehicle.get('year_detected') is None
                        or vehicle.get('mileage_km') is None
                        or vehicle.get('vehicle_data_warnings') or not ident.get('model')):
                    continue
            except (TypeError, ValueError, AttributeError, OverflowError):
                continue
            rank = 2 if (ident.get('brand'), ident.get('model')) in target_models else 3
        else:
            # Do not spend unused slots merely to pad totals with extra bids,
            # unknown prices, clearly broken vehicles or incomplete references.
            continue
        if kind in caps and counts.get(kind, 0) >= caps[kind]:
            continue
        ranked.append((rank, pos, url, kind))
    added = 0
    for _, _, url, kind in sorted(ranked):
        if added >= max_added:
            break
        key = listing_id(url)
        if key in used or (kind in caps and counts.get(kind, 0) >= caps[kind]):
            continue
        used.add(key)
        selected.append(url)
        counts[kind] = counts.get(kind, 0) + 1
        added += 1
    return selected, added


def detail_supply_breakdown(found, facts, history, cooldown, now_ts,
                            cached_urls=None):
    """Count *request-eligible* stock by purpose, not raw discovered links.

    This separates viable purchase research from dealer-price references,
    bid-only listings and unknown-price records. No category proves a profit.
    """
    ready = choose_detail_links(
        found, history, cooldown, now_ts, limit=len(found),
        cached_urls=cached_urls)
    counts = {'affordable_fixed_non_dealer': 0,
              'affordable_unverified_search_hint': 0,
              'affordable_bid_or_request': 0,
              'dealer_or_expensive_reference': 0,
              'clear_nonrunning_risk': 0,
              'unknown_or_other': 0}
    for url in ready:
        fact = facts.get(url) or {}
        if not isinstance(fact, dict):
            fact = {}
        kind, price = fact.get('price_type'), fact.get('price')
        if fact.get('reserved') or obvious_nonrunning_search_card(fact):
            counts['clear_nonrunning_risk'] += 1
        elif over_purchase_mileage_limit(fact):
            counts['dealer_or_expensive_reference'] += 1
        elif fact.get('seller_type') == 'handelaar':
            counts['dealer_or_expensive_reference'] += 1
        elif (kind == 'FIXED' and isinstance(price, (int, float))
              and not isinstance(price, bool) and finite_numeric_state(price)
              and 350 <= price <= MAX_BUY_PRICE
              and not is_financing_offer(fact)):
            counts['affordable_fixed_non_dealer'] += 1
        elif affordable_unverified_search_price(fact):
            counts['affordable_unverified_search_hint'] += 1
        elif kind in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION'):
            counts['affordable_bid_or_request'] += 1
        elif kind == 'FIXED':
            counts['dealer_or_expensive_reference'] += 1
        else:
            counts['unknown_or_other'] += 1
    counts['total_request_eligible'] = len(ready)
    return counts


def safe_starvation_refills(current, review_cards, history, cooldown,
                            now_ts, budget, cached_urls=None):
    """Fill a few detail slots without bypassing the standard cooldown rules."""
    count = min(10, budget) - len(current)
    if count <= 0:
        return []
    current_ids = {listing_id(url) for url in current}
    pool = [url for url, row in review_cards.items()
            if listing_id(url) not in current_ids
            and isinstance(row, dict)
            and not obvious_nonrunning_search_card(row)
            and not over_purchase_mileage_limit(row)
            and row.get('seller_type') != 'handelaar']
    return choose_detail_links(pool, history, cooldown, now_ts,
                               limit=count, cached_urls=cached_urls)


def detail_eligibility_breakdown(found, history, cooldown, now_ts,
                                 cached_urls=None):
    """Explain why discovered links cannot be revisited this run.

    This diagnostic never requests pages or weakens the 6h/48h cooldown.
    """
    cached_ids = {listing_id(url) for url in (cached_urls or set())}
    checked = {}
    for url, value in history.items():
        key = listing_id(url)
        checked[key] = max(str(value), checked.get(key, ''))
    restricted = {}
    for url, value in cooldown.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not finite_numeric_state(value):
            continue
        key = listing_id(url)
        restricted[key] = max(value, restricted.get(key, 0))
    totals = {'unseen_ready': 0, 'recheck_ready': 0,
              'cached_waiting_48h': 0, 'uncached_waiting_6h': 0,
              'url_cooldown': 0, 'invalid_history': 0}
    seen_ids = set()
    now = datetime.fromtimestamp(now_ts, timezone.utc)
    for url in found:
        key = listing_id(url)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        if restricted.get(key, 0) > now_ts:
            totals['url_cooldown'] += 1
            continue
        raw = checked.get(key)
        if not raw:
            totals['unseen_ready'] += 1
            continue
        try:
            last = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if last.tzinfo is None or last > now:
                totals['invalid_history'] += 1
                continue
        except (TypeError, ValueError, OverflowError):
            totals['invalid_history'] += 1
            continue
        if now - last < timedelta(hours=48 if key in cached_ids else 6):
            totals['cached_waiting_48h' if key in cached_ids
                   else 'uncached_waiting_6h'] += 1
        else:
            totals['recheck_ready'] += 1
    totals['unique_discovered'] = len(seen_ids)
    totals['detail_eligible'] = totals['unseen_ready'] + totals['recheck_ready']
    return totals


def interleave_search_results(groups):
    """Fairly mix model searches so the detail budget is not consumed by first queries."""
    mixed = []
    seen_ids = set()
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index < len(group) and listing_id(group[index]) not in seen_ids:
                mixed.append(group[index])
                seen_ids.add(listing_id(group[index]))
    return mixed

def seller_priority(item, seller):
    facts = seller_evidence(seller)
    evidence = {**seller, **facts}
    if facts['seller_type'] == 'handelaar':
        evidence['seller_type'] = 'business'
    return {**base_seller_priority(item, evidence), **facts}


def marktplaats_checks(item):
    evidence = dict(item)
    evidence['description'] = str(item.get('description') or '') + '\n' + str(item.get('apk_text') or '')
    evidence['city'] = item.get('location')
    details = extra_checks(evidence)
    details.update(vehicle_facts(item))
    apk = str(item.get('apk_text') or '').lower()
    months = {'januari': 1, 'februari': 2, 'maart': 3, 'april': 4, 'mei': 5, 'juni': 6,
              'juli': 7, 'augustus': 8, 'september': 9, 'oktober': 10, 'november': 11, 'december': 12}
    match = re.search(r'(\d{1,2})[ -]+([a-z]+|\d{1,2})[ -]+(20\d{2})', apk)
    if match:
        try:
            day, month, year = match.groups()
            month = int(month) if month.isdigit() else months[month]
            expiry = datetime(int(year), month, int(day), tzinfo=timezone.utc).date()
            details['apk_expiry_date'] = expiry.isoformat()
            days_left = (expiry - datetime.now(timezone.utc).date()).days
            details['apk_days_remaining'] = days_left
            details['apk_status'] = ('ongunstig' if days_left < 0 else
                                     'verloopt_binnen_30_dagen' if days_left <= 30 else 'positieve_claim_onbevestigd')
        except (ValueError, KeyError):
            pass
    return details


def preserve_newer_price(row, observation, now):
    """Merge a separately verified price without refreshing the full-ad timestamp."""
    if (listing_id(row.get('url')) != listing_id(observation.get('url')) or
            row.get('price_type') != 'FIXED' or observation.get('price_type') != 'FIXED'):
        return dict(row)
    try:
        observed = datetime.fromisoformat(str(observation.get('price_checked_at')).replace('Z', '+00:00'))
        full = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
        existing = datetime.fromisoformat(str(row.get('price_checked_at') or row.get('checked_at')).replace('Z', '+00:00'))
        if not all(t.tzinfo for t in (observed, full, existing)) or not full <= existing < observed <= now:
            return dict(row)
    except (ValueError, TypeError):
        return dict(row)
    price = observation.get('price', observation.get('asking_price_eur'))
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not finite_numeric_state(price) or price <= 0:
        return dict(row)
    result = dict(row)
    for field in ('price_checked_at', 'price_source', 'price_update_source',
                  'previous_price_eur', 'price_drop_eur', 'price_history'):
        if field in observation:
            result[field] = observation[field]
    result['price'] = price
    if 'asking_price_eur' in result:
        result['asking_price_eur'] = price
    return result


def retain_recent_archive_records(records, limit=1200):
    """Keep genuinely latest full-page snapshots, not oldest dict insertion positions.

    Updating an existing dict key does not move it to the end. A simple
    list(merged.values())[-limit:] silently discarded refreshed vehicles
    from the comparison archive whenever it reached capacity.
    """
    def sort_key(row):
        try:
            checked = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
            timestamp = checked.timestamp() if checked.tzinfo else float('-inf')
        except (TypeError, ValueError, OverflowError):
            timestamp = float('-inf')
        return timestamp, listing_id(row.get('url'))

    rows = sorted((row for row in records if isinstance(row, dict) and row.get('url')),
                  key=sort_key, reverse=True)
    if limit <= 0:
        return []
    if len(rows) <= limit:
        return list(reversed(rows))
    # Protect the newest usable fixed-price references for less common models.
    # The remaining slots still go to the newest ads across all models.
    groups = {}
    cutoff = datetime.now(timezone.utc).timestamp() - 21 * 86400
    for row in rows:
        if sort_key(row)[0] < cutoff or row.get('price_type') != 'FIXED':
            continue
        price = row.get('price')
        if (isinstance(price, bool) or not isinstance(price, (int, float))
                or not finite_numeric_state(price) or not 350 <= price <= 12000):
            continue
        ident = market_identity(row)
        if not ident.get('model') or not ident.get('brand'):
            continue
        vehicle = vehicle_facts(row)
        if (vehicle['year_detected'] is None or vehicle['mileage_km'] is None
                or vehicle['vehicle_data_warnings']):
            continue
        groups.setdefault((ident['brand'], ident['model']), []).append(row)
    reserve = min(12, max(2, limit // max(2, len(groups) * 4)))
    chosen, used = [], set()
    for group in groups.values():
        for row in group[:reserve]:
            key = listing_id(row['url'])
            if key not in used and len(chosen) < limit:
                chosen.append(row)
                used.add(key)
    for row in rows:
        if len(chosen) >= limit:
            break
        key = listing_id(row['url'])
        if key not in used:
            chosen.append(row)
            used.add(key)
    return sorted(chosen, key=sort_key)


def comparison_research_shortlist(rows, now, limit=100, reserve=20):
    """Keep unfinished valuations visible to the next scan planner.

    The general top-100 can otherwise be entirely occupied by older, already
    compared adverts. This reserves up to 20 *research-only* entries without
    turning missing comparisons or bidding floors into confirmed deals.
    """
    rows = [row for row in rows if isinstance(row, dict)]
    def rank(row):
        try:
            score = int(row.get('score') or 0)
        except (TypeError, ValueError, OverflowError):
            score = 0
        category = row.get('buyer_queue')
        if not isinstance(category, str):
            category = ''
        return (row.get('seller_type') != 'particulier',
                bool(row.get('severe_technical_risk')),
                not bool(row.get('max_purchase_price_eur')),
                {'prijscheck_bellen': 0, 'onderhandelen_tot_maxbod': 1,
                 'goedkoop_onderhandelen': 2, 'prijs_opvragen': 3,
                 'verder_onderzoek': 3, 'beschrijving_controleren': 4,
                 'grote_prijskloof': 4, 'reparatieproject': 5,
                 'uitsluiten': 6, 'buiten_budget': 7}.get(category, 4),
                -score)
    ordered = sorted(rows, key=rank)
    def eligible(row):
        comparison = row.get('market_comparison')
        if (not isinstance(comparison, dict)
                or row.get('reserved') or row.get('severe_technical_risk')
                or row.get('buyer_queue') in ('uitsluiten', 'buiten_budget', 'reparatieproject')
                or comparison.get('status') != 'onvoldoende_vergelijkingen'
                or seller_evidence(row)['seller_type'] != 'particulier'
                or listing_requires_manual_description_check(row)
                or over_purchase_mileage_limit(row)):
            return False
        try:
            checked = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
            if checked.tzinfo is None or not timedelta(0) <= now - checked <= timedelta(hours=24):
                return False
        except (ValueError, TypeError, OverflowError):
            return False
        price = research_entry_price(row)
        if price is None:
            return False
        try:
            if not market_identity(row).get('model'):
                return False
            details = vehicle_facts(row)
            return (details['year_detected'] is not None and
                    details['mileage_km'] is not None and
                    not details['vehicle_data_warnings'])
        except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
            return False
    saved, seen = [], set()
    # A confirmed fixed asking price is more actionable research evidence
    # than a bid floor; both remain unverified as profitable opportunities.
    for row in sorted(ordered, key=lambda r: r.get('price_type') != 'FIXED'):
        if len(saved) >= min(reserve, limit):
            break
        url = row.get('url')
        if not approved_listing_brand(url) or listing_id(url) in seen or not eligible(row):
            continue
        saved.append(row)
        seen.add(listing_id(url))
    reserved_count = len(saved)
    for row in ordered:
        if len(saved) >= limit:
            break
        url = row.get('url')
        if not approved_listing_brand(url) or listing_id(url) in seen:
            continue
        saved.append(row)
        seen.add(listing_id(url))
    return sorted(saved, key=rank), reserved_count


def recent_review_candidates(previous, archive, current_urls, invalidated_urls, now):
    """Recover recent private leads from the archive before presentation limits."""
    merged = {}
    excluded_ids = {listing_id(u) for u in current_urls | invalidated_urls}
    for source, rows in (('previous', previous), ('archive', archive)):
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url = row.get('url')
            if not approved_listing_brand(url) or listing_id(url) in excluded_ids:
                continue
            if source == 'archive' and (seller_evidence(row)['seller_type'] != 'particulier'
                    or not approved_listing_brand(url) or row.get('price_type') not in ('FIXED', 'MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION')):
                continue
            try:
                checked = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
                if checked.tzinfo is None or not timedelta(0) <= now - checked <= timedelta(hours=24):
                    continue
            except ValueError:
                continue
            key = listing_id(url)
            if key not in merged:
                merged[key] = (checked, dict(row))
            else:
                previous_checked, previous_row = merged[key]
                if checked > previous_checked:
                    merged[key] = (checked, preserve_newer_price(row, previous_row, now))
                else:
                    merged[key] = (previous_checked, preserve_newer_price(previous_row, row, now))
    return [row for _, row in merged.values()]


def archive_research_links(records, last_checked, cooldown, now_ts, limit=18, search_facts=None):
    """Revisit promising archived ads instead of forgetting them after one scan.

    One migration read for incomplete old records, thereafter once per six
    hours; never override rate limits. Archived values are scheduling hints only.
    """
    candidates = []
    now = datetime.fromtimestamp(now_ts, timezone.utc)
    for row in records if isinstance(records, list) else []:
        if not isinstance(row, dict):
            continue
        url, price = row.get('url'), row.get('price')
        if not url or not approved_listing_brand(url) or cooldown.get(url, 0) > now_ts:
            continue
        if seller_evidence(row)['seller_type'] == 'handelaar':
            continue
        # Bid-only ads also need periodic seller/photo/availability refreshes.
        if row.get('price_type') in ('MIN_BID', 'FAST_BID'):
            floor = row.get('minimum_bid_eur') or 0
            if (not isinstance(floor, (int, float)) or isinstance(floor, bool)
                    or not finite_numeric_state(floor) or floor < 0):
                continue
            price = max(350, floor)
        if not isinstance(price, (int, float)) or not 350 <= price <= MAX_BUY_PRICE or is_financing_offer(row):
            continue
        try:
            checked = datetime.fromisoformat(str(last_checked.get(url) or row.get('checked_at')).replace('Z', '+00:00'))
            hours = 0 if row.get('extraction_version') != 3 else 6
            if checked.tzinfo is None or now - checked < timedelta(hours=hours):
                continue
        except (ValueError, TypeError):
            continue
        details = marktplaats_checks(row)
        comparison = market_comparison(row, allow_legacy=True)
        rating = quality_gate(row, assess(row), details, comparison)
        if rating.get('severe_technical_risk') or comparison.get('high_price_dispersion'):
            continue
        margin = rating.get('indicative_margin_eur')
        if (isinstance(margin, (int, float)) and margin >= 250) or (price <= 1750 and seller_evidence(row)['seller_type'] == 'particulier'):
            priority_value = margin or 0
            if row.get('price_type') in ('MIN_BID', 'FAST_BID') and rating.get('max_purchase_price_eur'):
                priority_value = rating['max_purchase_price_eur'] - (row.get('minimum_bid_eur') or 0)
            candidates.append((-priority_value, price, url))
    selected = [url for _, _, url in sorted(candidates)[:limit]]
    by_url = {r.get('url'): r for r in records if isinstance(r, dict)}
    # New private candidates must not wait six hours for their comparison data.
    # Refresh references independently of whether the candidate itself is due.
    targets = []
    for row in by_url.values():
        if seller_evidence(row)['seller_type'] != 'particulier' or row.get('reserved'):
            continue
        bid_only = row.get('price_type') in ('MIN_BID', 'FAST_BID')
        price = (row.get('minimum_bid_eur') or 350) if bid_only else row.get('price')
        if not isinstance(price, (int, float)) or not 350 <= price <= MAX_BUY_PRICE:
            continue
        if row.get('severe_technical_risk') or listing_requires_manual_description_check(row):
            continue
        try:
            checked = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
            if checked.tzinfo is None or not timedelta(0) <= now - checked <= timedelta(days=2):
                continue
        except ValueError:
            continue
        targets.append((bid_only, price, row.get('url'), row))
    references = []
    reference_groups = []
    for _, _, url, row in sorted(targets, key=lambda x: x[:3]):
        verified = market_comparison(row)
        needed = max(0, 3 - verified.get('sample_size', 0))
        if needed == 0:
            continue
        comparison = market_comparison(row, allow_legacy=True)
        group = []
        for ref in comparison.get('comparable_urls', []):
            other = by_url.get(ref, {})
            fact = (search_facts or {}).get(ref, {})
            if (ref in selected or not approved_listing_brand(ref) or cooldown.get(ref, 0) > now_ts
                    or other.get('extraction_version') == 3
                    or fact.get('price_type') in ('MIN_BID', 'FAST_BID', 'ON_REQUEST')
                    or fact.get('reserved')):
                continue
            # A legacy reference may have failed or turned into a bid-only ad.
            # Do not repeatedly fetch it just because it is still in the archive.
            if ref not in choose_detail_links([ref], last_checked, cooldown, now_ts, limit=1):
                continue
            group.append(ref)
        group.sort(key=lambda ref: (search_facts or {}).get(ref, {}).get('price_type') != 'FIXED')
        if group:
            reference_groups.append((needed, row.get('price_type') not in ('MIN_BID', 'FAST_BID'), url, group))
    # Finish near-complete valuations first instead of giving one reference to
    # many cars and leaving every candidate below the three-comparison minimum.
    for needed, _, _, group in sorted(reference_groups):
        additions = [ref for ref in group if ref not in references][:needed]
        if len(references) + len(additions) <= 12:
            references.extend(additions)
    return selected + references[:12]

def prioritize_undelivered_purchase_details(selected, groups, history, cooldown,
                                             now_ts, facts, delivered_ids, limit,
                                             cached_urls=None, priorities=None):
    """Allocate up to 80% of existing detail slots to unsent affordable cars.

    Purely local queue reordering/refilling: the discovery and HTTP request
    ceilings, five-second pacing and 403/429 protection are unchanged. Keep
    the remaining slots for independent price comparisons and older research.
    If new eligible stock is genuinely exhausted, retain the original plan.
    """
    selected = list(selected)[:limit]
    if not delivered_ids or not groups or limit <= 0:
        return selected, 0
    # Delivery checkpoints use "marktplaats:m123", whereas the scanner's
    # listing_id(url) returns "m123". Compare the SAME canonical identity,
    # not the raw strings: otherwise confirmed previous ads are promoted
    # again at the expense of genuine discoveries.
    delivered_ids = {
        key[len("marktplaats:"):]
        for key in delivered_ids
        if isinstance(key, str) and re.fullmatch(r"marktplaats:[ma][0-9]+", key)
    }
    facts = facts if isinstance(facts, dict) else {}
    eligible = select_diverse_detail_links(
        groups, history, cooldown, now_ts,
        limit=min(sum(len(group) for group in groups), limit * 4),
        cached_urls=cached_urls, priorities=priorities, facts=facts)
    priority_types = ('affordable_fixed', 'unverified_price_hint')
    # Original choices keep their rank; new alternatives can replace only
    # old/dealer/reference slots, never push aside stronger already-selected
    # never-delivered purchase candidates.
    best = []
    used = set()
    for url in selected + eligible:
        key = listing_id(url)
        if (key in used or key in delivered_ids or
                detail_research_category(url, facts) not in priority_types):
            continue
        fact = facts.get(url)
        if not isinstance(fact, dict) or unsafe_for_purchase_shortlist(fact):
            continue
        best.append(url)
        used.add(key)
    budget = max(1, (limit * 4) // 5)
    promoted = best[:budget]
    used = {listing_id(url) for url in promoted}
    remainder = []
    for url in selected:
        key = listing_id(url)
        if key not in used:
            remainder.append(url)
            used.add(key)
    result = (promoted + remainder)[:limit]
    selected_ids = {listing_id(url) for url in selected}
    added = sum(listing_id(url) not in selected_ids for url in result)
    return result, added


def main():
    started = time.monotonic()
    run_now = datetime.now(timezone.utc)
    pending = load_pending(run_now)
    try:
        history = json.loads(HISTORY.read_text(encoding='utf-8'))
        if not isinstance(history, dict):
            history = {}
    except (OSError, ValueError):
        history = {}
    try:
        last_checked = json.loads(LAST_CHECKED.read_text(encoding='utf-8'))
        if not isinstance(last_checked, dict):
            last_checked = {}
    except (OSError, ValueError):
        last_checked = dict(history)  # Migration from first-seen-only storage.
    try:
        cooldown = json.loads(COOLDOWN.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cooldown = {}
    if not isinstance(cooldown, dict):
        cooldown = {}
    # Only the authenticated, acknowledged private delivery history defines
    # a repeat. Rechecking a previously delivered page can be useful for price
    # comparisons, but it must not crowd out new cheap cars this scan.
    from hunter_delivery_delta import (
        load as load_delivery_checkpoint, delivered_ids)
    delivered_to_pro = delivered_ids(load_delivery_checkpoint(Path.cwd()))
    # JSON booleans are integers in Python; never interpret them as expiry
    # timestamps or retry counts. Reject invalid persisted key types too.
    cooldown = {k: v for k, v in cooldown.items()
                if isinstance(k, str) and isinstance(v, (int, float))
                and finite_numeric_state(v)}
    now_ts = time.time()
    cooldown = {k: v for k, v in cooldown.items()
                if finite_numeric_state(v)
                and (v > now_ts or k == BLOCK_COUNT_KEY)}
    global_backoff = cooldown.get(GLOBAL_BACKOFF_KEY, 0)
    run_blocked = global_backoff > now_ts
    found = []
    seen = set()
    per_brand = {}
    search_groups = []
    search_errors = 0
    search_requests = 0
    extra_search_pages = 0
    detail_errors = 0
    # Model-specific and motivated-seller searches diversify expensive generic results.
    # Keep URL format unchanged: unverified price-filter parameters are unreliable.
    # Rotate search terms so all models are covered across successive runs.
    now = datetime.now(timezone.utc)
    # Offset uses the absolute UTC date, not just the clock hour; otherwise
    # the same small subset repeats every day.
    scan_slot = int(now.timestamp() // 7200)
    # Rotate the starting query; coprime stride 11 visits all 24 priority
    # models before repeating. Consecutive runs overlap less.
    offset = (scan_slot * 11) % len(PRIORITY_QUERIES)
    # Two priority-model slots can replace poor-yield expensive-model slots.
    # This never increases search request ceilings or drops secondary coverage.
    previous_for_split = {}
    try:
        previous_for_split = json.loads(STATUS.read_text(encoding='utf-8'))
        if not isinstance(previous_for_split, dict):
            previous_for_split = {}
    except (OSError, ValueError, TypeError):
        previous_for_split = {}
    primary_count, secondary_count = affordable_search_pool_split(
        previous_for_split)
    primary_coverage = primary_count - 2
    secondary_coverage = secondary_count - 2
    selected_queries = [PRIORITY_QUERIES[(offset + i) % len(PRIORITY_QUERIES)]
                        for i in range(primary_coverage)]
    selected_queries += [OTHER_QUERIES[((scan_slot * 5) + i) % len(OTHER_QUERIES)]
                         for i in range(secondary_coverage)]
    previous_status = {}
    try:
        previous_status = json.loads(STATUS.read_text(encoding='utf-8'))
        if not isinstance(previous_status, dict):
            previous_status = {}
        previous_metrics = previous_status.get('query_metrics', {})
        if not isinstance(previous_metrics, dict):
            previous_metrics = {}
    except (OSError, ValueError, TypeError, AttributeError):
        previous_status = {}
        previous_metrics = {}
    detail_budget = adaptive_detail_budget(previous_status)
    # A rolling history is less sensitive to a single lucky scan.
    previous_history = previous_status.get('query_metrics_history', [])
    if not isinstance(previous_history, list):
        previous_history = []
    metric_history = [m for m in previous_history[-7:] if isinstance(m, dict)]
    # Newer status files already include their latest metrics in history.
    if previous_metrics and (not metric_history or metric_history[-1] != previous_metrics):
        metric_history.append(previous_metrics)
    metric_history = metric_history[-7:]

    for pool, slots in ((PRIORITY_QUERIES, 2), (OTHER_QUERIES, 2)):
        ranked = [(historical_affordable_query_yield(q, metric_history), q)
                  for q in pool if q not in selected_queries]
        ranked = [(score, q) for score, q in ranked if score is not None]
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected_queries.extend(q for _, q in ranked[:slots])
        # No historical evidence? Fall back to deterministic rotating coverage.
        for step in range(len(pool)):
            if sum(q in pool for q in selected_queries) >= (
                primary_count if pool is PRIORITY_QUERIES else secondary_count):
                break
            candidate = pool[(scan_slot + step) % len(pool)]
            if candidate not in selected_queries:
                selected_queries.append(candidate)
    # Load reference history before scheduling searches, so the backlog planner
    # knows which records require the longer 48-hour revisit interval.
    try:
        cached_records = json.loads(ARCHIVE.read_text(encoding='utf-8'))
        if not isinstance(cached_records, list):
            cached_records = []
        cached_urls = {row['url'] for row in cached_records
                       if isinstance(row, dict) and isinstance(row.get('url'), str)}
    except (OSError, ValueError, TypeError):
        cached_records = []
        cached_urls = set()
    affordable_backlog_ready_at_start = len(ready_purchase_backlog(
        pending, last_checked, cooldown, run_now, cached_urls=cached_urls))
    # Reallocate a few existing searches towards motivated sellers and
    # age-specific models only when genuinely NEW affordable cards run dry.
    # Discovery remains capped at SEARCHES_PER_RUN; preserve broad coverage.
    discovery_variant_count = adaptive_discovery_variant_count(
        previous_status, affordable_backlog_ready_at_start)
    for i in range(min(discovery_variant_count, primary_count)):
        pos = (scan_slot + i * 3) % primary_count
        selected_queries[pos] = choose_motivated_query(
            selected_queries[pos], metric_history, scan_slot+i)
    selected_queries = backlog_search_plan(
        selected_queries, pending, last_checked, cooldown, run_now,
        previous_status, cached_urls=cached_urls)
    try:
        prior_candidates = json.loads(OUTPUT.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        prior_candidates = []
    comparison_targets = comparison_search_plan(prior_candidates, previous_status, run_now)
    reference_targets = comparison_reference_targets(prior_candidates, run_now)
    selected_queries = list(dict.fromkeys(list(comparison_targets) + selected_queries))[:SEARCHES_PER_RUN]
    previous_query_history = previous_status.get('comparison_search_history')
    if not isinstance(previous_query_history, dict):
        previous_query_history = {}
    comparison_search_history = {
        q: t for q, t in previous_query_history.items()
        if isinstance(q, str) and isinstance(t, (int, float))
        and not isinstance(t, bool) and finite_numeric_state(t)
        and 0 <= run_now.timestamp() - t < 7 * 86400
    }
    selected_second_pages = select_second_page_queries(selected_queries, metric_history)

    search_cards_skipped_over_budget = 0
    search_cards_with_visible_price = 0
    search_cards_with_year = 0
    search_cards_with_mileage = 0
    search_cards_considered = 0
    search_cards_budget_eligible = 0
    search_cards_missing_price = 0
    search_cards_missing_bid_or_request = 0
    search_cards_missing_fixed_price = 0
    search_cards_missing_unknown_type = 0
    search_cards_lease_hints = 0
    search_cards_over_purchase_mileage = 0
    price_diagnostics = {'card_container_found': 0, 'anchor_only': 0,
                         'price_from_card': 0, 'price_from_anchor': 0,
                         'price_from_attribute': 0, 'price_from_jsonld': 0,
                         'price_from_anchor_fallback': 0,
                         'missing_with_card': 0, 'missing_without_card': 0,
                         'missing_card_has_euro': 0, 'missing_card_has_digits': 0,
                         'conflicting_euro_prices': 0,
                         'missing_price_examples': []}
    card_priorities = {}
    checked_listing_ids_for_search = {listing_id(url) for url in last_checked}
    search_facts = {}
    # Separately record ALL current platform observations (also reserved ads).
    # The purchase selection is deduplicated; review invalidation is not.
    live_search_facts = {}
    listing_query = {}
    card_review_candidates = {}
    query_metrics = {}
    market_gap_rank_cache = {}
    def market_gap_rank(link, fact, card_text):
        if link not in market_gap_rank_cache:
            market_gap_rank_cache[link] = search_card_market_gap_rank(link, fact, card_text)
        return market_gap_rank_cache[link]
    blocked_phase = 'cooldown' if run_blocked else None
    blocked_http_status = None
    for query_index, term in enumerate(selected_queries):
        if global_backoff > time.time() or time.monotonic() - started >= MAX_RUN_SECONDS:
            break
        url = BASE + '/q/' + quote(term) + '/'
        try:
            search_requests += 1
            html, final, status = fetch(url)
            if term in comparison_targets:
                comparison_search_history[term] = time.time()
            page = parse(html)
            second_page_ids = set()
            second_page_fetched = False
            # Follow a real pagination link to reach beyond sponsored first-page ads.
            if query_index in selected_second_pages:
                next_url = next((urljoin(BASE, h) for h in page.links
                                 if '/q/' in h and '/p/2/' in h
                                 and urlparse(urljoin(BASE, h)).hostname == 'www.marktplaats.nl'), None)
                if next_url:
                    time.sleep(REQUEST_DELAY_SECONDS)
                    search_requests += 1
                    more, _, _ = fetch(next_url)
                    # Pagination often repeats sponsored ads from page one.
                    # Only count genuinely additional listing IDs to assess
                    # whether page-two requests are worth future scan slots.
                    extra_page = parse(more)
                    first_urls = list(page.links) + list(search_page_records(page.scripts))
                    extra_urls = list(extra_page.links) + list(search_page_records(extra_page.scripts))
                    # JSON-LD keys are often absolute URLs, while HTML links
                    # can be relative. Normalize both before comparing IDs.
                    first_ids = {listing_id(u) for h in first_urls
                                 for u in [normalize_link(h)] if u and approved_listing_brand(u)}
                    second_page_ids = {listing_id(u) for h in extra_urls
                                       for u in [normalize_link(h)] if u and approved_listing_brand(u)} - first_ids
                    second_page_fetched = True
                    page = parse(html + more)
                    extra_search_pages += 1

            new = 0
            query_links = []
            seen_on_page = 0
            priced_on_page = 0
            affordable_private_on_page = 0
            unseen_affordable_private_on_page = 0
            second_page_new_affordable = 0
            above_purchase_mileage_on_page = 0
            skipped_on_page = 0
            structured_search_prices = search_structured_prices(page)
            facts = search_page_records(page.scripts)
            live_search_facts.update(facts)
            # The same listing often appears in several searches. Do NOT let a
            # later query's duplicate JSON overwrite the first accepted live
            # price type, seller or search-card hint.
            hrefs = list(dict.fromkeys(list(facts) + page.links))
            def card_rank(href):
                link = normalize_link(href)
                card_text = page.card_texts.get(link) or page.link_texts.get(href, '')
                return ranked_search_card(
                    facts.get(link), card_text,
                    already_checked=listing_id(link) in checked_listing_ids_for_search,
                    market_gap_rank=market_gap_rank(link, facts.get(link), card_text))
            hrefs.sort(key=card_rank)
            for href in hrefs:
                link = normalize_link(href)
                if link and approved_listing_brand(link) and listing_id(link) not in seen:
                    seen_on_page += 1
                    search_cards_considered += 1
                    container_text = page.card_texts.get(link)
                    anchor_text = page.link_texts.get(href, '')
                    card_text = container_text or anchor_text
                    if container_text:
                        price_diagnostics['card_container_found'] += 1
                    else:
                        price_diagnostics['anchor_only'] += 1
                    signals = search_card_signals(card_text)
                    fact = facts.get(link, {})
                    if fact.get('reserved'):
                        continue
                    if fact:
                        # Bids are not asking prices. A partial FIXED-price
                        # record must not erase a clear public card price.
                        platform_price = fact.get('price')
                        kind = fact.get('price_type')
                        if kind and kind != 'FIXED':
                            signals['price'] = None
                        elif isinstance(platform_price, (int, float)):
                            signals['price'] = platform_price
                        for field in ('year', 'mileage_km'):
                            raw = fact.get(field)
                            if str(raw or '').isdigit():
                                signals[field] = int(raw)
                    anchor_price = search_card_signals(anchor_text)['price']
                    if signals['price'] is not None:
                        price_diagnostics['price_from_card' if container_text else 'price_from_anchor'] += 1
                    # Prefer an explicit structured price on the listing anchor.
                    # Attribute values may be plain numbers rather than '€' text.
                    raw_card_price = page.link_attributes.get(href, {}).get('data-price')
                    # General mention of "lease mogelijk" does not turn a
                    # fixed-price sale into a monthly-payment advertisement.
                    financing_hint = is_financing_offer({
                        'title': str(fact.get('title') or link.rsplit('/', 1)[-1].replace('-', ' ')),
                        'price': signals['price'],
                        'description': card_text,
                    })
                    fixed_or_unspecified = not fact or fact.get('price_type') in (None, 'FIXED')
                    if fixed_or_unspecified and not financing_hint and signals['price'] is None:
                        # A card may contain other amounts while the anchor
                        # holds a single, clear asking price. Never use this
                        # fallback for MIN_BID/FAST_BID or request-only ads.
                        if anchor_price is not None and 350 <= anchor_price <= 12000:
                            signals['price'] = anchor_price
                            price_diagnostics['price_from_anchor_fallback'] += 1
                    if fixed_or_unspecified and not financing_hint and signals['price'] is None and raw_card_price:
                        structured_price = parse_price_eur(raw_card_price)
                        if structured_price is not None and 350 <= structured_price <= 12000:
                            signals['price'] = structured_price
                            price_diagnostics['price_from_attribute'] += 1
                    if fixed_or_unspecified and not financing_hint and signals['price'] is None:
                        signals['price'] = structured_search_prices.get(link)
                        if signals['price'] is not None:
                            price_diagnostics['price_from_jsonld'] += 1
                    if signals['price'] is None:
                        price_diagnostics['missing_with_card' if container_text else 'missing_without_card'] += 1
                        if '€' in card_text or re.search(r'(?i)\bEUR\b', card_text):
                            price_diagnostics['missing_card_has_euro'] += 1
                        if re.search(r'\d', card_text):
                            price_diagnostics['missing_card_has_digits'] += 1
                        if len(price_diagnostics['missing_price_examples']) < 8:
                            # Only short, escaped price neighborhoods; no full listing HTML.
                            snippets = [card_text[max(0, m.start()-24):m.start()+65]
                                        for m in list(re.finditer(r'€|\bEUR\b', card_text, flags=re.I))[:2]]
                            price_diagnostics['missing_price_examples'].append({
                                'card_text_length': len(card_text),
                                'price_contexts': snippets,
                                'has_card': bool(container_text),
                                'anchor_text_length': len(anchor_text),
                            })
                        amounts = re.findall(r'(?:€\s*|\bEUR\s+)([0-9][0-9.,]*)', card_text, flags=re.I)
                        if len({p for p in (parse_price_eur(a) for a in amounts) if p is not None}) > 1:
                            price_diagnostics['conflicting_euro_prices'] += 1
                    # Store only an explicitly observed search-card hint in
                    # the persistent queue. It is never a verified asking price.
                    if (signals['price'] is not None and 350 <= signals['price'] <= MAX_BUY_PRICE
                            and fixed_or_unspecified and not financing_hint):
                        search_facts[link] = {
                            **fact, 'search_card_price_hint_eur': signals['price'],
                            'search_card_price_unverified': True,
                            'search_card_year_hint': signals['year'],
                            'search_card_mileage_hint_km': signals['mileage_km']}
                    if signals['price'] is not None:
                        search_cards_with_visible_price += 1
                        priced_on_page += 1
                    else:
                        search_cards_missing_price += 1
                        if fact.get('price_type') in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION'):
                            search_cards_missing_bid_or_request += 1
                        elif fact.get('price_type') == 'FIXED':
                            search_cards_missing_fixed_price += 1
                        else:
                            search_cards_missing_unknown_type += 1
                    if re.search(r'(?i)lease|per maand|termijnbedrag|huurkoop', card_text):
                        search_cards_lease_hints += 1
                    if signals['year'] is not None:
                        search_cards_with_year += 1
                    if signals['mileage_km'] is not None:
                        search_cards_with_mileage += 1
                    # Known >330k km stock stays usable for pricing research,
                    # but must not inflate affordable *purchase* query yield.
                    above_purchase_mileage = over_purchase_mileage_limit(fact, card_text)
                    if above_purchase_mileage:
                        above_purchase_mileage_on_page += 1
                        search_cards_over_purchase_mileage += 1
                    if clearly_over_budget_search_card(card_text):
                        search_cards_skipped_over_budget += 1
                        skipped_on_page += 1
                        continue
                    unseen_before_detail = listing_id(link) not in checked_listing_ids_for_search
                    affordable_private_card = (
                        not above_purchase_mileage and
                        fixed_or_unspecified and not financing_hint and
                        fact.get('seller_type') != 'handelaar' and
                        not obvious_nonrunning_search_card(fact) and
                        isinstance(signals['price'], (int, float)) and
                        not isinstance(signals['price'], bool) and
                        350 <= signals['price'] <= MAX_BUY_PRICE)
                    if affordable_private_card:
                        affordable_private_on_page += 1
                        if unseen_before_detail:
                            unseen_affordable_private_on_page += 1
                            if listing_id(link) in second_page_ids:
                                second_page_new_affordable += 1
                    seen.add(listing_id(link))
                    if (fixed_or_unspecified and not financing_hint and
                            signals['price'] is not None and
                            350 <= signals['price'] <= MAX_BUY_PRICE):
                        search_cards_budget_eligible += 1
                    # A bid floor is not a fixed asking price. Do not promote
                    # MIN_BID/FAST_BID/ON_REQUEST cards using visible amounts.
                    card_priorities[link] = (
                        (0, signals['price'])
                        if fixed_or_unspecified and not financing_hint and
                           signals['price'] is not None and
                           350 <= signals['price'] <= MAX_BUY_PRICE
                        else search_card_priority(card_text))
                    if (fixed_or_unspecified and not financing_hint and
                            signals['price'] is not None and
                            350 <= signals['price'] <= MAX_BUY_PRICE and
                            signals['year'] is not None and
                            signals['mileage_km'] is not None):
                        card_priorities[link] = (-1, signals['price'])
                    if (fixed_or_unspecified and
                            fact.get('seller_type') != 'handelaar'
                            and not fact.get('reserved')
                            and not above_purchase_mileage
                            and not obvious_nonrunning_search_card(fact)
                            and signals['price'] is not None and 350 <= signals['price'] <= MAX_BUY_PRICE
                            and not financing_hint
                            and not any(term in (link.rsplit('/', 1)[-1].replace('-', ' ')).lower()
                                        for term in ('gevraagd', 'gezocht', 'inkoop', 'wij kopen', 'auto gezocht'))):
                        card_review_candidates[link] = {
                            'url': link, 'price': signals['price'], 'year': signals['year'],
                            'mileage_km': signals['mileage_km'], 'search_query': term,
                            'title': fact.get('title') or link.rsplit('/', 1)[-1].replace('-', ' '),
                            'seller_type': fact.get('seller_type') or 'onbekend',
                            'seller_name': fact.get('seller_name'),
                            'seller_platform_type': fact.get('seller_platform_type'),
                            'price_type': fact.get('price_type') or 'UNKNOWN',
                            'image_urls': fact.get('image_urls') or [],
                            'source': 'marktplaats_search_card', 'verified': False,
                            'profit_estimate_eur': None, 'manual_review_required': True,
                            'reason': 'Zoekkaart binnen inkoopbudget; advertentie, APK en marktwaarde nog controleren',
                            'discovered_at': run_now.isoformat()
                        }
                    # Spend detail budget on possible private sales; dealers remain reference data.
                    if fact.get('seller_type') == 'handelaar':
                        card_priorities[link] = (5, signals['price'] or 99999)
                    elif (fixed_or_unspecified and not financing_hint and
                          signals['price'] is not None and
                          350 <= signals['price'] <= MAX_BUY_PRICE
                          and not obvious_nonrunning_search_card(fact)):
                        card_priorities[link] = (-2, signals['price'])
                    card_priorities[link] = discovery_priority(fact, card_priorities[link])
                    if above_purchase_mileage:
                        card_priorities[link] = (6, signals['price'] or 99999)
                    gap = (None if above_purchase_mileage
                           else market_gap_rank(link, fact, card_text))
                    if gap is not None:
                        card_priorities[link] = (min(card_priorities[link][0], gap),
                                                 card_priorities[link][1])
                    # Retain the observed odometer hint even when there is no
                    # usable card price; subsequent selection must not treat
                    # this >330k reference as a potential purchase.
                    # Keep the unverified card hint when present, otherwise
                    # store facts only for this actually accepted listing.
                    search_facts.setdefault(link, {
                        **fact, **({'search_card_mileage_hint_km': signals['mileage_km']}
                                  if signals['mileage_km'] is not None else {})
                    })
                    listing_query[link] = term
                    query_links.append(link)
                    new += 1
                    if new >= PER_QUERY:
                        break
            # Only reorder candidates; missing card details never exclude a car.
            query_links.sort(key=lambda link: card_priorities.get(link, (2, 0)))
            search_groups.append(query_links)
            per_brand[term] = new
            query_metrics[term] = {'http_status': status, 'eligible_links': new,
                                   'unique_cards_seen': seen_on_page,
                                   'priced_cards': priced_on_page,
                                   'affordable_private_cards': affordable_private_on_page,
                                   'unseen_affordable_private_cards': unseen_affordable_private_on_page,
                                   'above_purchase_mileage_cards': above_purchase_mileage_on_page,
                                   'over_budget_skipped': skipped_on_page}
            if second_page_fetched:
                query_metrics[term].update({
                    'second_page_unique_listing_cards': len(second_page_ids),
                    'second_page_unseen_affordable_cards': second_page_new_affordable})
            print(f'{term}: HTTP {status}; {new} nieuwe mogelijke links', flush=True)
        except HTTPError as e:
            if e.code in (403, 429, 503):
                run_blocked = True
                global_backoff = checkpoint_restriction(e, cooldown, time.time())
                blocked_phase = 'search'
                blocked_http_status = e.code
                query_metrics[term] = {'http_status': e.code, 'eligible_links': 0}
                break
            search_errors += 1
            print(f'{term}: HTTP {e.code}, overgeslagen', flush=True)
        except (URLError, TimeoutError, OSError) as e:
            search_errors += 1
            print(f'{term}: netwerkfout {type(e).__name__}', flush=True)
        except (TypeError, ValueError, AttributeError, KeyError, OverflowError) as e:
            # A malformed public search page must not abort the entire scan.
            search_errors += 1
            query_metrics[term] = {'http_status': 'parse_error', 'eligible_links': 0}
            print(f'{term}: fout bij uitlezen ({type(e).__name__})', flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)

    # The same cached archive loaded for the search planner also drives
    # detail cooldown, avoiding inconsistent scheduling decisions.
    # Never let stale pending data replace fresh price-type, seller or ranking.
    # Use every real search-page observation for invalidation, including
    # reserved/overbudget ads omitted from the buy-candidate shortlist.
    merge_pending_search_state(pending, search_facts, card_priorities)
    pending_by_brand = {}
    for url in pending:
        pending_by_brand.setdefault(urlparse(url).path.split('/')[3], []).append(url)
    search_groups.extend(pending_by_brand.values())
    # Interleave brands before applying the limited detail-request budget.
    found = interleave_search_results(search_groups)
    unique_discovered = len(set(found))
    detail_budget = min(detail_budget, MAX_TOTAL_REQUESTS - search_requests)
    # Prefer links never seen before, but still revisit a small sample of known ads.
    detail_links = select_diverse_detail_links(search_groups, last_checked, cooldown, time.time(),
                                              cached_urls=cached_urls,
                                              priorities=card_priorities, facts=search_facts,
                                              limit=detail_budget)
    # Cap untargeted dealer/expensive pages; targeted references have their own bounded slots.
    purchase_links = [u for u in detail_links if card_priorities.get(u, (2, 0))[0] < 5 and not (isinstance(search_facts.get(u, {}).get('price'), (int, float)) and search_facts[u]['price'] > MAX_BUY_PRICE)]
    general_reference_links = [u for u in detail_links if u not in purchase_links][:16]
    # Reserve up to ten slots for unknown-price discoveries, even when cheap cards dominate.
    exploration_pool = [u for u, f in search_facts.items()
                        if f.get('price') is None and f.get('seller_type') != 'handelaar'
                        and not over_purchase_mileage_limit(f)]
    exploration_links = choose_detail_links(exploration_pool, last_checked, cooldown, time.time(), limit=min(10, detail_budget//10), cached_urls=cached_urls)
    detail_links = interleave_search_results([exploration_links + purchase_links + general_reference_links])[:detail_budget]
    # Apply exactly the same 6h/48h revisit eligibility as normal details.
    # The former 2h emergency fallback could re-fetch archived pages too early.
    starvation_rechecks = 0
    if len(detail_links) < 10 and found and not run_blocked:
        added = safe_starvation_refills(
            detail_links, card_review_candidates, last_checked, cooldown,
            time.time(), detail_budget, cached_urls=cached_urls)
        detail_links.extend(added)
        starvation_rechecks = len(added)
    archive_priority = archive_research_links(cached_records, last_checked, cooldown, time.time(), search_facts=search_facts) if not run_blocked else []
    price_change_rechecks = []
    cached_by_id = {listing_id(r['url']): r for r in cached_records
                    if isinstance(r, dict) and isinstance(r.get('url'), str)
                    and approved_listing_brand(r['url'])}
    for url, fact in search_facts.items():
        old = cached_by_id.get(listing_id(url), {})
        if (fact.get('price_type') != 'FIXED' or old.get('price_type') != 'FIXED'
                or fact.get('seller_type') == 'handelaar'
                or over_purchase_mileage_limit(fact)
                or over_purchase_mileage_limit(old)):
            continue
        price, previous_price = fact.get('price'), old.get('price')
        if not all(isinstance(v, (int, float)) for v in (price, previous_price)) or not 350 <= price <= MAX_BUY_PRICE or price >= previous_price:
            continue
        try:
            checked = datetime.fromisoformat(str(old.get('checked_at')).replace('Z', '+00:00'))
            if checked.tzinfo is None or run_now - checked < timedelta(hours=1):
                continue
        except ValueError:
            continue
        if choose_detail_links([url], {}, cooldown, time.time(), limit=1):
            price_change_rechecks.append((price - previous_price, url))
    changed_links = [url for _, url in sorted(price_change_rechecks)[:10]]
    reference_links = targeted_reference_links(
        search_facts, reference_targets, last_checked, cooldown, time.time(),
        limit=8, cached_urls=cached_urls)
    independent_reference_links = fixed_reference_discovery_links(
        search_groups, search_facts, last_checked, cooldown, time.time(),
        cached_urls=cached_urls, limit=min(12, max(4, detail_budget // 8))
    ) if not run_blocked else []
    shortlist = []
    for path in (HOTLIST, BIDLIST):
        try:
            rows = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(rows, list):
                shortlist.extend(rows)
        except (OSError, ValueError):
            pass
    refresh_links = shortlist_refresh_links(
        shortlist, last_checked, cooldown, run_now,
        cached_urls=cached_urls) if not run_blocked else []
    # Bound intentional reference work; reserve most capacity for purchase candidates.
    detail_links = interleave_search_results([
        refresh_links + changed_links + reference_links +
        independent_reference_links + archive_priority[:12] + detail_links
    ])[:detail_budget]
    detail_count_before_refill = len(detail_links)
    detail_links = refill_underused_detail_budget(
        detail_links, search_groups, last_checked, cooldown, time.time(),
        detail_budget, facts=search_facts, priorities=card_priorities,
        cached_urls=cached_urls)
    detail_slots_refilled = len(detail_links) - detail_count_before_refill
    detail_links, detail_selection_mix = bound_detail_research_mix(
        detail_links, search_facts, detail_budget)
    # Reuse a few slots released by the final cap without more requests than
    # already budgeted. Price references favor models with pending valuations.
    reference_model_targets = set()
    for target in reference_targets.values():
        if isinstance(target, dict):
            ident = market_identity(target)
            if ident.get('brand') and ident.get('model'):
                reference_model_targets.add((ident['brand'], ident['model']))
    before_post_mix_refill = len(detail_links)
    detail_links, post_mix_slots_refilled = refill_after_research_caps(
        detail_links, search_groups, search_facts, last_checked, cooldown,
        time.time(), detail_budget, cached_urls=cached_urls,
        target_models=reference_model_targets)
    for url in detail_links[before_post_mix_refill:]:
        kind = detail_research_category(url, search_facts)
        detail_selection_mix[kind] = detail_selection_mix.get(kind, 0) + 1
    detail_links, new_delivery_refills = prioritize_undelivered_purchase_details(
        detail_links, search_groups, last_checked, cooldown, time.time(),
        search_facts, delivered_to_pro, detail_budget, cached_urls=cached_urls,
        priorities=card_priorities)
    detail_selection_mix['new_delivery_refills'] = new_delivery_refills
    detail_selection_mix['total_selected'] = len(detail_links)
    detail_selection_mix['post_mix_slots_refilled'] = post_mix_slots_refilled
    eligible_detail_links = len(detail_links)
    detail_queue_eligibility = detail_eligibility_breakdown(
        found, last_checked, cooldown, time.time(), cached_urls=cached_urls)
    detail_supply_at_selection = detail_supply_breakdown(
        found, search_facts, last_checked, cooldown, time.time(),
        cached_urls=cached_urls)
    checked_listing_ids_at_start = {listing_id(u) for u in last_checked}
    new_detail_links_planned = sum(listing_id(u) not in checked_listing_ids_at_start for u in detail_links)
    recheck_detail_links_planned = eligible_detail_links - new_detail_links_planned
    # Distinguish new-to-scanner, new-to-delivery and old research checks.
    # These aggregate counters require NO extra Marktplaats requests and
    # contain no seller, vehicle, advert URL or other personal data.
    new_delivery_detail_links_planned = sum(
        "marktplaats:" + listing_id(link) not in delivered_to_pro
        for link in detail_links)
    already_delivered_detail_links_planned = (
        eligible_detail_links - new_delivery_detail_links_planned)
    # Keep an honest, useful review queue even when recently checked detail
    # pages cannot be fetched again. This is NOT a verified deal list.
    review_path = Path('marktplaats_handmatig_controleren.json')
    try:
        prior_review_cards = json.loads(review_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        prior_review_cards = []
    valid_prior_review_cards, review_cards_invalidated_by_live_search = (
        prune_superseded_search_reviews(prior_review_cards, live_search_facts))
    card_queue = retain_recent_search_card_reviews(
        list(card_review_candidates.values()), valid_prior_review_cards, run_now)
    write_json_state(review_path, card_queue)

    # A starved queue is diagnostic: never bypass cooldown or a 403/429.
    # Lower revisit intervals above permit healthy periodic rescans.
    results = []
    rejected = 0
    rejection_reasons = {'excluded_terms': 0, 'missing_price': 0, 'outside_price_range': 0, 'suspicious_price': 0}
    rejected_old = 0
    comparison_only = []
    attempted_detail_requests = 0
    invalidated_urls = set()
    for link in detail_links:
        if run_blocked or global_backoff > time.time() or time.monotonic() - started >= MAX_RUN_SECONDS:
            break
        if cooldown.get(link, 0) > time.time():
            continue
        attempted_detail_requests += 1
        try:
            html, final, status = fetch(link)
            if not normalize_link(final) or listing_id(final) != listing_id(link):
                invalidated_urls.add(link)
                continue
            data = extract_fields(parse(html), listing_url=link)
            if data.get('unavailable'):
                invalidated_urls.add(link)
                cooldown[link] = time.time() + 7*86400
                last_checked[link] = datetime.now(timezone.utc).isoformat()
                continue
            if not data.get('title') or (data.get('price') is None and not data.get('price_type')):
                # A partial HTTP-200 response is not proof that the car vanished.
                detail_errors += 1
                cooldown[link] = time.time() + 1800
                last_checked[link] = datetime.now(timezone.utc).isoformat()
                continue
            invalidated_urls.add(link)
            bid_only = data.get('price_type') in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION')
            price = (data.get('minimum_bid_eur') or 0) if bid_only else data.get('price')
            title = str(data.get('title') or '').lower()
            # Record every successfully opened page, including rejected ads.
            # Otherwise expensive listings are fetched again every scan.
            first_seen_before_fetch = history.get(link)
            history[link] = first_seen_before_fetch or datetime.now(timezone.utc).isoformat()
            last_checked[link] = datetime.now(timezone.utc).isoformat()
            if any(term in title for term in EXCLUDE_TERMS) or is_financing_offer(data) or '/bestelauto-s/' in link:
                rejected += 1
                rejection_reasons['excluded_terms'] += 1
                continue
            if not isinstance(price, (int, float)):
                rejected += 1
                rejection_reasons['missing_price'] += 1
                continue
            if (not bid_only and not 350 <= price <= 12000) or (bid_only and price > MAX_BUY_PRICE) or data.get('reserved'):
                rejected += 1
                rejection_reasons['outside_price_range'] += 1
                continue
            if not bid_only and price < 500 and year_hint_new(title):
                rejected += 1
                rejection_reasons['suspicious_price'] += 1
                continue
            year = extract_vehicle_year(data.get('year'), data.get('title'))
            if year is None or year < MIN_YEAR:
                rejected_old += 1
                # Older or unknown-year cars stay visible for manual review.
                data['older_or_unknown_year'] = True
            data['year'] = year
            data.update({'url': final, 'source': 'marktplaats',
                         'checked_at': datetime.now(timezone.utc).isoformat(),
                         'estimated_profit': None, 'verified': False})
            data['price_checked_at'] = data['checked_at']
            first_seen = first_seen_before_fetch
            data['new_to_hunter'] = not bool(first_seen)
            data['first_seen_at'] = first_seen or data['checked_at']
            history[link] = first_seen or data['checked_at']
            details = marktplaats_checks(data)
            data.update(details)
            if price > MAX_BUY_PRICE:
                comparison_only.append(data)
                continue
            comparison = market_comparison({**data, **details})
            rating = quality_gate(data, assess(data), details, comparison)
            data.update({**details, **rating, 'market_comparison': comparison})
            data.update(seller_priority(data, data))
            data['purchase_review_tier'] = purchase_review_tier(data)
            results.append(data)
            print(f"Advertentie: HTTP {status}; prijs={data['price']}; titel={str(data['title'])[:65]}", flush=True)
        except HTTPError as e:
            if e.code in (404, 410):
                invalidated_urls.add(link)
                cooldown[link] = time.time() + 7 * 86400
                last_checked[link] = datetime.now(timezone.utc).isoformat()
                continue
            if e.code in (403, 429, 503):
                run_blocked = True
                global_backoff = checkpoint_restriction(e, cooldown, time.time())
                blocked_phase = 'detail'
                blocked_http_status = e.code
                break
            cooldown[link] = time.time() + max(1200, retry_after_seconds(e.headers, time.time()))
            detail_errors += 1
            print(f'Advertentie HTTP {e.code}: {link}', flush=True)
        except (URLError, TimeoutError, OSError) as e:
            cooldown[link] = time.time() + 1200
            detail_errors += 1
            print(f'Advertentie netwerkfout {type(e).__name__}: {link}', flush=True)
        except (TypeError, ValueError, AttributeError, KeyError, OverflowError) as e:
            # A single broken ad should be quarantined rather than crash the run.
            # Keep partial-error status visible; do not invent a missing sale price.
            cooldown[link] = time.time() + 3600
            detail_errors += 1
            print(f'Advertentie uitleesfout {type(e).__name__}: {link}', flush=True)
        finally:
            time.sleep(REQUEST_DELAY_SECONDS)

    if not run_blocked and search_requests:
        cooldown.pop(BLOCK_COUNT_KEY, None)
        cooldown.pop(GLOBAL_BACKOFF_KEY, None)
    remaining = prepare_pending_rows(
        found, pending, search_facts, card_priorities, last_checked,
        invalidated_urls, seen, run_now)
    pending_queue_eligibility = detail_eligibility_breakdown(
        [row['url'] for row in remaining], last_checked, cooldown, time.time(),
        cached_urls=cached_urls)
    pending_affordable_fixed_count = sum(
        isinstance(row.get('facts'), dict)
        and row['facts'].get('price_type') == 'FIXED'
        and isinstance(row['facts'].get('price'), (int, float))
        and not isinstance(row['facts'].get('price'), bool)
        and 350 <= row['facts']['price'] <= MAX_BUY_PRICE
        and row['facts'].get('seller_type') != 'handelaar'
        and not row['facts'].get('reserved')
        and not obvious_nonrunning_search_card(row['facts'])
        for row in remaining)
    write_json_state(PENDING, remaining)
    write_json_state(COOLDOWN, cooldown)
    write_json_state(HISTORY, dict(list(history.items())[-4000:]))
    write_json_state(LAST_CHECKED, dict(list(last_checked.items())[-4000:]))
    try:
        old = json.loads(ARCHIVE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        old = []
    merged = {listing_id(row['url']): row for row in sorted(
        [r for r in old if isinstance(r, dict) and approved_listing_brand(r.get('url'))],
        key=lambda r: str(r.get('checked_at') or ''))} if isinstance(old, list) else {}
    # A price-only refresh may be newer than the archive's full-page snapshot.
    try:
        price_observations = json.loads(OUTPUT.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        price_observations = []
    for observation in price_observations if isinstance(price_observations, list) else []:
        if isinstance(observation, dict) and approved_listing_brand(observation.get('url')):
            key = listing_id(observation['url'])
            if key in merged:
                merged[key] = preserve_newer_price(merged[key], observation, datetime.now(timezone.utc))
    old_by_id = dict(merged)
    invalidated_ids = {listing_id(u) for u in invalidated_urls}
    merged = {key: row for key, row in merged.items() if key not in invalidated_ids}
    for row in results + comparison_only:
        key = listing_id(row['url'])
        previous_row = old_by_id.get(key, {})
        old_price, new_price = previous_row.get('price'), row.get('price')
        observations = list(previous_row.get('price_history') or [])
        if previous_row.get('price_type') == row.get('price_type') == 'FIXED' and old_price != new_price and isinstance(old_price, (int, float)):
            observations.append({'price_eur': old_price, 'checked_at': previous_row.get('checked_at')})
            row['previous_price_eur'] = old_price
            row['price_drop_eur'] = max(0, old_price - new_price)
        row['price_history'] = observations[-12:]
        row['first_seen_at'] = previous_row.get('first_seen_at') or row.get('first_seen_at')
        if previous_row:
            row['new_to_hunter'] = False
        merged[key] = row
    archive = retain_recent_archive_records(merged.values())
    write_json_state(ARCHIVE, archive)
    # Re-evaluate after updating the reference archive, so this run's other
    # independent listings can serve as comparables (never the ad itself).
    for row in results:
        comparison = market_comparison(row)
        rating = quality_gate(row, assess(row), row, comparison)
        row.update({**rating, 'market_comparison': comparison, **deal_watchlist(row, rating, comparison)})
        row.update(seller_priority(row, row))
        row.update(buying_opportunity(row))
        # Keep promising listings visible for manual review, but never treat a
        # short/summary description as proof of technical condition.
        row['description_review_required'] = listing_requires_manual_description_check(row)
        if row['description_review_required']:
            row['actionable_lead'] = False
            row['buyer_reason'] = (str(row.get('buyer_reason') or '') +
                '; volledige beschrijving en technische staat controleren')
    for row in results:
        row['purchase_review_tier'] = purchase_review_tier(row)
    tier_rank = {'particulier_eerst_bekijken': 0, 'handmatig_beoordelen': 1,
                 'bouwjaar_controleren': 2, 'ouder_boven_voorkeursprijs': 3,
                 'handelaar_lage_prioriteit': 4, 'technisch_risico': 5,
                 'buiten_budget': 6}
    watch_rank = {'sterke_prijsafwijking_controleren': 0, 'prijsafwijking_controleren': 1,
                  'goedkope_oudere_auto_controleren': 2, 'handmatig_prijsonderzoek': 3, 'voertuiggegevens_controleren': 4, 'geen_duidelijke_prijsafwijking': 4,
                  'oud_boven_voorkeursprijs': 5, 'uitsluiten': 6, 'buiten_budget': 7}
    buyer_rank = {'prijs_opvragen': 3, 'prijscheck_bellen': 0, 'onderhandelen_tot_maxbod': 1, 'goedkoop_onderhandelen': 2, 'verder_onderzoek': 3, 'beschrijving_controleren': 4, 'grote_prijskloof': 4,
                  'reparatieproject': 5, 'uitsluiten': 6, 'buiten_budget': 7}
    results.sort(key=lambda x: (buyer_rank.get(x.get('buyer_queue'), 4), not x['actionable_lead'],
                                watch_rank.get(x.get('watchlist_tier'), 6),
                                tier_rank.get(x['purchase_review_tier'], 7),
                                -int(x.get('score') or 0),
                                not x['new_to_hunter']))
    # Re-score cached shortlist rows offline using the newest risk and seller rules.
    # Cached rows are not represented as freshly verified listings.
    # Keep a larger rolling shortlist, without presenting older listings as freshly checked.
    previous = []
    try:
        previous = json.loads(OUTPUT.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    if not isinstance(previous, list):
        previous = []
    current_urls = {row.get('url') for row in results}
    older = recent_review_candidates(previous, archive, current_urls, invalidated_urls, datetime.now(timezone.utc))
    older, cached_reviews_superseded_by_live_search = prune_superseded_cached_reviews(
        older, live_search_facts)
    cached_rescored = 0
    for row in older:
        fact = search_facts.get(row.get('url'), {})
        if fact.get('image_urls') and not row.get('image_urls'):
            row['image_urls'] = fact['image_urls']
            row['image_url'] = fact['image_urls'][0]
        row.update(seller_priority(row, row))
        if is_financing_offer(row):
            row['watchlist_tier'] = 'uitsluiten'
            row['watchlist_reason'] = 'Maandtermijn of leaseaanbieding'
            row['buyer_queue'] = 'uitsluiten'
            row['buyer_reason'] = 'Maandtermijn of leaseaanbieding'
            row['actionable_lead'] = False
            row['not_rechecked_this_run'] = True
            continue
        if isinstance(row.get('price'), (int, float)) or row.get('price_type') in ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION'):
            details = marktplaats_checks(row)
            comparison = market_comparison(row)
            row['market_comparison'] = comparison
            rating = quality_gate(row, assess(row), details, comparison)
            row.update({**details, **rating, **deal_watchlist(row, rating, comparison)})
            row.update(seller_priority(row, row))
            row.update(buying_opportunity(row))
            row['purchase_review_tier'] = purchase_review_tier(row)
            cached_rescored += 1
        row['not_rechecked_this_run'] = True
        row['actionable_lead'] = False
        # Preserve the risk label; cached ads still require a live recheck.
        row['cached_review_priority'] = row.get('priority')
        if row.get('priority') != 'technisch_risico':
            row['priority'] = 'opnieuw_controleren'
    for row in results:
        row['not_rechecked_this_run'] = False
    older.sort(key=lambda x: (buyer_rank.get(x.get('buyer_queue'), 4), watch_rank.get(x.get('watchlist_tier'), 6),
                              x.get('priority') == 'technisch_risico',
                              x.get('seller_type') == 'handelaar',
                              -int(x.get('score') or 0)))
    older = [row for row in older if row.get('buyer_queue') != 'uitsluiten']
    visible, retained_comparison_research = comparison_research_shortlist(
        results + older, datetime.now(timezone.utc), limit=100, reserve=20)
    write_json_state(OUTPUT, visible)
    # Separate fresh, actionable review list from the historical 100-ad research archive.
    hotlist = []
    recent_cached = []
    for row in older:
        try:
            checked = datetime.fromisoformat(str(row.get('checked_at')).replace('Z', '+00:00'))
            if checked.tzinfo and timedelta(0) <= datetime.now(timezone.utc) - checked <= timedelta(hours=24):
                recent_cached.append(row)
        except (ValueError, TypeError):
            continue
    for row in results + recent_cached:
        row.update(seller_priority(row, row))
        if row.get('seller_type') != 'particulier' or row.get('price_type') != 'FIXED' or row.get('reserved'):
            continue
        if not approved_listing_brand(row.get('url') or '') or listing_requires_manual_description_check(row):
            continue
        if (unsafe_for_purchase_shortlist(row)
                or row.get('buyer_queue') in ('uitsluiten', 'buiten_budget', 'reparatieproject')):
            continue
        price = row.get('price')
        comparison = row.get('market_comparison') or {}
        median = comparison.get('median_asking_price_eur')
        sample = comparison.get('sample_size', 0)
        bid = row.get('max_purchase_price_eur')
        # A low absolute price is NOT evidence of a bargain. No comparison,
        # no koopkans: retain such ads only in the separate research archive.
        if (comparison.get('status') != 'indicatieve_vraagprijzen' or
                not isinstance(sample, int) or sample < 3 or
                not isinstance(median, (int, float)) or median <= 0 or
                not isinstance(price, (int, float))):
            continue
        discount = (median - price) / median
        gap = median - price
        # Require a substantial gap from like-for-like asking prices.
        if comparison.get('high_price_dispersion') or discount < 0.20 or gap < 350:
            continue
        # Conservative estimated margin must support EUR 500 at asking price,
        # or within a modest and explicitly identified negotiation range.
        if not isinstance(bid, (int, float)) or bid <= 0:
            continue
        if price <= bid:
            label = 'binnen_biedlimiet_inspecteren'
        elif price - bid <= 200 and (price - bid) / price <= 0.10:
            label = 'realistisch_onderhandelen'
        else:
            continue
        review_price = model_review_price(row)
        if review_price is not None and price > review_price:
            continue
        hotlist.append({**{field: row[field] for field in (
                            'price_checked_at', 'price_source', 'price_update_source',
                            'previous_price_eur', 'price_drop_eur', 'price_history',
                            'gross_margin_eur', 'net_margin_before_tax_eur',
                            'vehicle_data_warnings', 'seller_classification_reasons',
                            'market_comparison', 'estimated_cost_breakdown_eur', 'risk_category', 'repair_review_required') if field in row},
                        'url': row.get('url'), 'title': row.get('title'),
                        'price_type': row.get('price_type'), 'description': row.get('description'),
                        'description_source': row.get('description_source'),
                        'seller_platform_type': row.get('seller_platform_type'),
                        'seller_name': row.get('seller_name'), 'location': row.get('location'),
                        'image_urls': row.get('image_urls', []), 'image_url': row.get('image_url'),
                        'year': row.get('year'), 'mileage_km': row.get('mileage_km'),
                        'asking_price_eur': price, 'max_purchase_price_eur': bid,
                        'user_model_review_price_eur': review_price,
                        'negotiation_needed_eur': row.get('required_negotiation_eur'),
                        'indicative_resale_eur': row.get('indicative_resale_eur'),
                        'estimated_costs_eur': row.get('estimated_costs_allowance_eur'),
                        'target_profit_eur': minimum_profit(), 'seller_type': row.get('seller_type'),
                        'profit_unverified': True,
                        'price_benchmark_type': ('gebruikersrichtprijs_vraagprijs' if review_price is not None else None),
                        'checked_at': row.get('checked_at'), 'category': label,
                        'not_rechecked_this_run': bool(row.get('not_rechecked_this_run')),
                        'availability_check_required': True,
                        'missing_information': row.get('missing_information', []),
                        'description_review_required': listing_requires_manual_description_check(row),
                        'verification_status': ('volledige_beschrijving_controleren'
                                                if row.get('description_review_required', True)
                                                else 'technische_inspectie_nodig'),
                        'verified_profit_eur': None,
                        'price_gap_eur': row.get('asking_price_gap_eur'),
                        'price_gap_pct': row.get('asking_price_discount_pct'),
                        'comparison_sample_size': sample,
                        'comparable_urls': comparison.get('comparable_urls', []),
                        'comparison_median_asking_eur': median,
                        'discount_vs_comparables_pct': round(discount * 100),
                        'buyer_reason': row.get('buyer_reason'),
                        'note': ('Controleer technische staat, actuele beschikbaarheid en vergelijkingsadvertenties. '
                                 'Geen gegarandeerde verkoopwaarde of winst.')})
    hotlist.sort(key=lambda x: (bool(x['description_review_required']),
                                 0 if x['category'] == 'binnen_biedlimiet_inspecteren'
                                 else 1 if x['category'] == 'realistisch_onderhandelen'
                                 else 2 if x['category'] == 'prijsafwijking_onderzoeken'
                                 else 3 if x['category'] == 'modelprijs_inspecteren' else 4,
                                 x['asking_price_eur']))
    # Diversify the *presentation* without suppressing legitimate same-model deals.
    # Round-robin across vehicle models within each evidence tier.
    from car_triage import vehicle_identity
    buckets = {}
    for lead in hotlist:
        model = vehicle_identity({'title': lead.get('title')})['model'] or 'onbekend'
        buckets.setdefault(model, []).append(lead)
    diversified = []
    while any(buckets.values()):
        for model in list(buckets):
            if buckets[model]:
                diversified.append(buckets[model].pop(0))
    hotlist = diversified
    # A blocked run is not evidence that previously found listings vanished.
    scan_skipped = not attempted_detail_requests and not found and run_blocked
    if scan_skipped:
        try:
            previous_hotlist = json.loads(HOTLIST.read_text(encoding='utf-8'))
            if isinstance(previous_hotlist, list):
                hotlist = []
                for previous in previous_hotlist:
                    if not isinstance(previous, dict):
                        continue
                    previous = {**previous, **seller_evidence(previous)}
                    try:
                        checked = datetime.fromisoformat(str(previous.get('checked_at')).replace('Z', '+00:00'))
                        fresh = checked.tzinfo is not None and timedelta(0) <= datetime.now(timezone.utc) - checked <= timedelta(hours=24)
                    except ValueError:
                        fresh = False
                    if (fresh and previous.get('price_type') == 'FIXED'
                            and previous.get('seller_type') == 'particulier'
                            and not previous.get('reserved')
                            and not unsafe_for_purchase_shortlist(previous)
                            and not listing_requires_manual_description_check(previous)
                            and isinstance(previous.get('market_comparison'), dict)
                            and previous['market_comparison'].get('status') == 'indicatieve_vraagprijzen'
                            and isinstance(previous['market_comparison'].get('sample_size'), int)
                            and previous['market_comparison']['sample_size'] >= 3
                            and finite_numeric_state(previous.get('max_purchase_price_eur'))
                            and previous.get('max_purchase_price_eur', 0) > 0
                            and finite_numeric_state(previous.get('asking_price_eur'))
                            and previous.get('asking_price_eur', 0) > 0):
                        hotlist.append({**previous, 'not_rechecked_this_run': True})
        except (OSError, ValueError):
            pass
    bidlist = []
    for row in results + recent_cached:
        row.update(seller_priority(row, row))
        bid = row.get('max_purchase_price_eur')
        floor = row.get('minimum_bid_eur') or 0
        if (row.get('price_type') in ('MIN_BID', 'FAST_BID') and row.get('seller_type') == 'particulier'
                and not (row.get('market_comparison') or {}).get('high_price_dispersion')
                and not row.get('reserved') and not row.get('severe_technical_risk')
                and not unsafe_for_purchase_shortlist(row)
                and not listing_requires_manual_description_check(row)
                and isinstance(bid, (int, float)) and not isinstance(bid, bool)
                and finite_numeric_state(bid) and bid > 0
                and isinstance(floor, (int, float)) and not isinstance(floor, bool)
                and finite_numeric_state(floor) and 0 <= floor <= bid):
            bidlist.append({**row, 'category': 'prijsafspraak_nodig', 'asking_price_eur': None,
                            'profit_unverified': True, 'availability_check_required': True})
    write_json_state(BIDLIST, bidlist)
    write_json_state(HOTLIST, hotlist)
    # Attribute independently assessed opportunities to the search that found them.
    # This measures quality rather than merely how many links a query returns.
    for row in results:
        term = listing_query.get(row.get('url')) or listing_query.get(
            normalize_link(row.get('url') or ''))
        if term is None:
            continue
        metrics = query_metrics.get(term)
        if not metrics:
            continue
        metrics['details_assessed'] = metrics.get('details_assessed', 0) + 1
        comparison = row.get('market_comparison') or {}
        if (comparison.get('status') == 'indicatieve_vraagprijzen'
                and (row.get('actionable_lead') or row.get('watchlist_tier') == 'sterke_prijsafwijking_controleren')):
            metrics['quality_leads'] = metrics.get('quality_leads', 0) + 1
    scan_health = scan_health_label(
        run_blocked, search_errors, detail_errors, search_requests,
        attempted_detail_requests)
    price_coverage, price_coverage_warning = non_bid_price_coverage(
        search_cards_with_visible_price, search_cards_missing_fixed_price,
        search_cards_missing_unknown_type)
    write_json_state(STATUS, {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'status': ('stopped_by_marktplaats_restriction' if run_blocked else
                   'idle_no_requests' if not (search_requests or attempted_detail_requests) else
                   'candidates_found' if results else
                   'search_unavailable' if selected_queries and search_errors == len(selected_queries) else
                   'no_eligible_candidates'),
        'candidate_count': len(results), 'hotlist_count': len(hotlist), 'bid_opportunity_count': len(bidlist),
        'max_purchase_mileage_km': MAX_PURCHASE_MILEAGE_KM,
        'above_purchase_mileage_limit_count': sum(over_purchase_mileage_limit(r) for r in results),
        'fixed_price_candidates': sum(r.get('price_type') == 'FIXED' for r in results),
        'bidding_or_request_candidates': sum(r.get('price_type') in
            ('MIN_BID', 'FAST_BID', 'ON_REQUEST', 'SEE_DESCRIPTION') for r in results),
        'fixed_private_candidates': sum(r.get('price_type') == 'FIXED' and r.get('seller_type') == 'particulier' for r in results),
        'fixed_private_candidates_with_market_comparisons': sum(
            r.get('price_type') == 'FIXED' and r.get('seller_type') == 'particulier'
            and (r.get('market_comparison') or {}).get('status') == 'indicatieve_vraagprijzen'
            for r in results),
        'candidates_with_photos': sum(bool(x.get('image_urls')) for x in results), 'short_or_unverified_descriptions': sum(listing_requires_manual_description_check(x) for x in results),
        'minimum_profit_target_eur': minimum_profit(),
        'price_change_priority_rechecks': len(changed_links),
        'price_drops_confirmed': sum(bool(r.get('price_drop_eur')) for r in results),
        'missing_price_reasons': {reason: sum(r.get('price_missing_reason') == reason for r in results) for reason in ('bieden_geen_vraagprijs', 'prijs_op_aanvraag_of_in_tekst', 'geen_eenduidige_verkoopprijs')},
        'no_fixed_opportunities_reason': None if hotlist else 'Geen verse particuliere vaste-prijsadvertenties voldoen tegelijk aan vergelijkbaarheid, risico-, korting- en margedrempels',
        'hotlist_tiers': {category: sum(x['category'] == category for x in hotlist) for category in ('binnen_biedlimiet_inspecteren', 'realistisch_onderhandelen', 'prijsafwijking_onderzoeken', 'goedkope_auto_marge_onbekend', 'modelprijs_inspecteren')},
        'visible_shortlist_count': len(visible),
        'retained_comparison_research_count': retained_comparison_research,
        'visible_fixed_private_comparison_pending': sum(
            row.get('price_type') == 'FIXED'
            and row.get('seller_type') == 'particulier'
            and (row.get('market_comparison') or {}).get('status') == 'onvoldoende_vergelijkingen'
            for row in visible),
        'previous_unchecked_count': sum(bool(r.get('not_rechecked_this_run')) for r in visible),
        'cached_ads_rescored': cached_rescored,
        'visible_candidates_with_comparisons': sum((r.get('market_comparison') or {}).get('status') == 'indicatieve_vraagprijzen' for r in visible),
        'visible_candidates_comparison_pending': sum((r.get('market_comparison') or {}).get('status') == 'onvoldoende_vergelijkingen' for r in visible),
        'buyer_queue_tiers': {tier: sum(x.get('buyer_queue') == tier for x in results)
                              for tier in buyer_rank},
        'manual_review_tiers': {tier: sum(x.get('purchase_review_tier') == tier for x in results)
                                for tier in tier_rank},
        'last_checked_history_count': len(last_checked),
        'comparison_only_count': len(comparison_only),
        'comparison_archive_count': len(archive),
        'comparison_archive_distinct_vehicle_models': len({
            (ident.get('brand'), ident.get('model'))
            for row in archive for ident in [market_identity(row)]
            if ident.get('model')}),
        'comparison_references_limited_by_same_dealer': sum(
            (x.get('market_comparison') or {}).get('excluded_same_dealer', 0)
            for x in results),
        'rejected_count': rejected,
        'rejection_reasons': rejection_reasons,
        'new_candidates': sum(x['new_to_hunter'] for x in results),
        'actionable_leads': sum(x['actionable_lead'] for x in results),
        'deal_watchlist_tiers': {tier: sum(x.get('watchlist_tier') == tier for x in results)
                                 for tier in watch_rank},
        'seller_types': {kind: sum(x['seller_type'] == kind for x in results) for kind in ('particulier', 'handelaar', 'onbekend')},
        'comparable_candidates': sum(x.get('market_comparison', {}).get('status') == 'indicatieve_vraagprijzen' for x in results),
        'comparison_pending': sum(x.get('market_comparison', {}).get('status') == 'onvoldoende_vergelijkingen' for x in results),
        'comparison_status_counts': {
            kind: sum((x.get('market_comparison') or {}).get('status') == kind for x in results)
            for kind in ('indicatieve_vraagprijzen', 'onvoldoende_vergelijkingen', 'onvoldoende_variantgegevens',
                         'onvoldoende_voertuiggegevens', 'tegenstrijdige_voertuiggegevens', 'model_onbekend')
        },
        'comparison_sample_counts': {
            str(n): sum((x.get('market_comparison') or {}).get('sample_size') == n for x in results)
            for n in (0, 1, 2)
        },
        'comparison_exclusion_totals': {
            reason: sum((x.get('market_comparison') or {}).get('exclusions', {}).get(reason, 0) for x in results)
            for reason in ('vehicle_data', 'price_type_or_reserved', 'price', 'finance',
                           'condition', 'year_or_mileage', 'brand')
        },
        'search_errors': search_errors, 'detail_errors': detail_errors,
        'global_backoff_active': global_backoff > time.time(),
        'blocked_this_run': run_blocked,
        'scan_health': scan_health,
        'requests_attempted_total': search_requests + attempted_detail_requests,
        'request_delay_seconds': REQUEST_DELAY_SECONDS, 'detail_request_ceiling': MAX_PER_RUN,
        'total_request_ceiling': MAX_TOTAL_REQUESTS,
        'search_requests_saved_by_backlog': SEARCHES_PER_RUN - len(selected_queries),
        'pending_detail_count': len(remaining),
        'pending_queue_eligibility_after_scan': pending_queue_eligibility,
        'pending_affordable_fixed_count': pending_affordable_fixed_count,
        'pending_rediscovered_on_search_count': sum(
            listing_id(row['url']) in seen for row in remaining),
        'affordable_fixed_backlog_ready_at_start': affordable_backlog_ready_at_start,
        'run_time_limit_reached': time.monotonic() - started >= MAX_RUN_SECONDS,
        'blocked_phase': blocked_phase,
        'blocked_http_status': blocked_http_status,
        'global_backoff_until': datetime.fromtimestamp(global_backoff, timezone.utc).isoformat() if global_backoff > time.time() else None,
        'active_url_cooldowns': sum(k not in (GLOBAL_BACKOFF_KEY, BLOCK_COUNT_KEY) and v > time.time() for k, v in cooldown.items()),
        'links_discovered': len(found),
        'search_cards_with_visible_price': search_cards_with_visible_price,
        'search_cards_considered': search_cards_considered,
        'search_cards_budget_eligible': search_cards_budget_eligible,
        'search_cards_missing_price': search_cards_missing_price,
        'search_cards_missing_bid_or_request': search_cards_missing_bid_or_request,
        'search_cards_missing_fixed_price': search_cards_missing_fixed_price,
        'search_cards_missing_unknown_type': search_cards_missing_unknown_type,
        'search_cards_lease_hints': search_cards_lease_hints,
        'search_cards_over_purchase_mileage': search_cards_over_purchase_mileage,
        'unique_links_discovered': unique_discovered,
        'eligible_detail_links': eligible_detail_links,
        'detail_queue_eligibility': detail_queue_eligibility,
        'detail_supply_at_selection': detail_supply_at_selection,
        'detail_selection_mix': detail_selection_mix,
        'detail_slots_refilled': detail_slots_refilled,
        'detail_post_mix_slots_refilled': post_mix_slots_refilled,
        'detail_count_before_refill': detail_count_before_refill,
        'new_detail_links_planned': new_detail_links_planned,
        'recheck_detail_links_planned': recheck_detail_links_planned,
        'new_delivery_detail_links_planned': new_delivery_detail_links_planned,
        'already_delivered_detail_links_planned': already_delivered_detail_links_planned,
        'starvation_rechecks': starvation_rechecks,
        'archive_priority_rechecks': len(archive_priority),
        'shortlist_refresh_scheduled': len(refresh_links),
        'exploration_details_scheduled': len(exploration_links),
        'general_reference_details_scheduled': len(general_reference_links),
        'targeted_reference_details_scheduled': len(reference_links),
        'reference_targets_without_query_cooldown': len(reference_targets),
        'independent_fixed_reference_details_scheduled': len(independent_reference_links),
        'comparison_search_queries': list(comparison_targets),
        'comparison_search_history': comparison_search_history,
        'full_page_descriptions': sum(x.get('description_source') == 'pagina_tekst' for x in results),
        'search_card_manual_review_count': len(card_queue),
        'review_cards_invalidated_by_live_search': review_cards_invalidated_by_live_search,
        'cached_reviews_superseded_by_live_search': cached_reviews_superseded_by_live_search,
        'manual_review_carried_over_count': sum(bool(r.get('not_rechecked_this_run')) for r in card_queue),
        'manual_review_invalidated_by_live_search': review_cards_invalidated_by_live_search,
        'manual_review_new_this_run': sum(not r.get('not_rechecked_this_run') for r in card_queue),
        'manual_review_with_year': sum(row['year'] is not None for row in card_queue),
        'manual_review_with_mileage': sum(row['mileage_km'] is not None for row in card_queue),
        'detail_queue_empty_due_to_history': bool(found and not detail_links),
        'detail_budget_utilization_pct': round(100 * attempted_detail_requests / detail_budget, 1) if detail_budget else 0,
        'detail_queue_starved': eligible_detail_links < max(5, detail_budget // 5),
        'price_diagnostics': price_diagnostics,
        'search_price_detection_pct': round(100 * search_cards_with_visible_price / search_cards_considered, 1) if search_cards_considered else None,
        'non_bidding_price_detection_pct': price_coverage,
        'search_price_extraction_warning': price_coverage_warning,
        'market_gap_priority_cards': sum(rank is not None for rank in market_gap_rank_cache.values()),
        'search_cards_with_year': search_cards_with_year,
        'search_cards_with_mileage': search_cards_with_mileage,
        'search_cards_skipped_over_budget': search_cards_skipped_over_budget,
        'cached_urls_available': len(cached_urls),
        'queries_used': selected_queries, 'links_by_brand': per_brand,
        'second_page_queries_selected': [q for i, q in enumerate(selected_queries) if i in selected_second_pages],
        'affordable_private_search_cards': sum(m.get('affordable_private_cards', 0) for m in query_metrics.values()),
        'unseen_affordable_private_search_cards': sum(m.get('unseen_affordable_private_cards', 0) for m in query_metrics.values()),
        'query_metrics': query_metrics,
        'detail_selection_strategy': 'best_per_query_then_global_rank',
        'query_rotation_slot_hours': 2,
        'uncached_revisit_hours': 6,
        'cached_revisit_hours': 48,
        'query_metrics_history': (metric_history + [query_metrics])[-8:],
        'searches_per_run': len(selected_queries),
        'motivated_queries_used': [q for q in selected_queries
            if any(q.endswith(' '+suffix) for suffix in DISCOVERY_TERMS)],
        'discovery_variant_experiments_planned': discovery_variant_count,
        'discovery_year_query_count': sum(
            q.endswith(' '+suffix) for q in selected_queries
            for suffix in YEAR_SEARCH_TERMS),
        'searches_per_run_ceiling': SEARCHES_PER_RUN, 'links_per_search_cap': PER_QUERY,
        'planned_priority_model_searches': primary_count,
        'planned_secondary_model_searches': secondary_count,
        'search_pages_requested': search_requests, 'extra_search_pages': extra_search_pages,
        'detail_pages_attempted': attempted_detail_requests,
        # A budget cap or seller filter is not a recent-history cooldown.
        'links_skipped_by_cooldown_or_recent_history': sum(
            detail_queue_eligibility.get(k, 0)
            for k in ('cached_waiting_48h', 'uncached_waiting_6h',
                      'url_cooldown', 'invalid_history')),
        'request_eligible_links_not_selected': max(
            0, detail_queue_eligibility.get('detail_eligible', 0) - eligible_detail_links),
        'affordable_fixed_detail_supply_exhausted': (
            detail_supply_at_selection.get('affordable_fixed_non_dealer', 0) == 0
            and detail_supply_at_selection.get('affordable_unverified_search_hint', 0) == 0),
        'detail_request_budget': detail_budget,
        'rejected_old_or_unknown_year': rejected_old,
        'preferred_min_year': MIN_YEAR, 'older_max_price_eur': OLDER_MAX_PRICE,
        'max_buy_price_eur': MAX_BUY_PRICE,
        'note': 'Proefscan van openbare pagina’s; geen volledige marktdekking of winstgarantie.'
    })
    known_prices = sum(x['price'] is not None for x in results)
    print(f'KLAAR: {len(results)} advertenties bekeken; {known_prices} prijzen uitgelezen.', flush=True)
    print(f'Opgeslagen als {OUTPUT}. Dit zijn gefilterde kandidaten, geen geverifieerde koopadviezen.', flush=True)

if __name__ == '__main__':
    main()
