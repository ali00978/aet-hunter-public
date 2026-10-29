"""Read-only Facebook Marketplace candidates from saved permitted provider results.

The Facebook collector workflows stay unchanged: this module makes *zero*
Facebook/API requests and cannot override quota or access restrictions.
"""
import hashlib
import json
import os
import math
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from car_triage import HIGH_RISK, PREFERRED
from marktplaats_data import parse_mileage
from facebook_vehicle_filter import is_vehicle_title, provider_photos

ROOT=Path(os.environ.get("AET_HUNTER_STATE_DIR", Path(__file__).resolve().parent))
SOURCES=(("apify_kandidaten.json","apify_status.json","Apify"),
         ("socialfetch_kandidaten.json","socialfetch_status.json","Social Fetch"))
ID_RE=re.compile(r"^/marketplace/item/([0-9]+)/?$")
MAX_AGE=timedelta(days=62)
BLOCKED=tuple(HIGH_RISK)+("motor schade","motor beschadigd","motor kapot","voor onderdelen","onderdelen auto","sloopauto","niet rijdbaar","loopt niet")
TRADE_BRANDS = (
    "volkswagen", "vw", "opel", "peugeot", "renault", "ford", "toyota",
    "fiat", "suzuki", "hyundai", "kia", "bmw", "audi", "mercedes",
    "mercedes-benz", "seat", "skoda", "škoda", "citroen", "citroën",
)
# Titles can omit a brand entirely. Only highly distinctive known trade models.
TRADE_MODELS = (
    "polo", "corsa", "clio", "aygo", "picanto", "twingo", "punto",
    "alto", "swift", "fabia", "ibiza", "astra", "fiesta", "i10", "i20",
    "yaris", "golf", "agila", "panda", "c1", "c3", "206", "207",
)
OTHER_MAKES = (
    "volvo", "alfa romeo", "honda", "nissan", "mazda", "dacia", "saab",
    "mitsubishi", "chevrolet", "daewoo", "subaru", "tesla", "land rover",
    "range rover", "jeep", "lancia", "chrysler",
)
TECHNICAL_WARNINGS = (
    "motorstoring", "koppakking", "olie verbruik", "olieverbruik",
    "koelvloeistof verlies", "koelvloeistofverlies", "slipt",
    "automaat kapot", "airco kapot", "motor tikt", "motor ratelt",
    "rookt", "distributie defect", "versnellingsbak probleem",
)
LOCAL_HINTS = (
    "dronten", "almere", "lelystad", "zeewolde", "kampen",
    "zwolle", "harderwijk", "biddinghuizen", "swifterbant",
    "emmelord", "nijkerk", "amersfoort",
)
def has_word(text, word):
    """Match whole case-insensitive model/brand/place terms, never fragments."""
    text = str(text or "").casefold()
    word = str(word or "").casefold()
    return bool(word and re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text))

def is_trade_model(title):
    """Never mix unrelated brands into this dealership's 16-brand shortlist."""
    title = str(title or "").casefold()
    if any(has_word(title, w) for w in OTHER_MAKES):
        return False
    return any(has_word(title, w) for w in TRADE_BRANDS+TRADE_MODELS)

def trading_score(title, price, year, mileage, seller, photos, posted, location, description, now):
    """Transparent prioritization, NOT a claimed verified EUR 500 resale profit."""
    text = str(title or "").casefold()
    score = 38
    signals = []
    if any(has_word(text, word) for word in PREFERRED):
        score += 18
    if price <= 1200: score += 11
    elif price <= 1900: score += 7
    elif price >= 2700: score -= 13
    elif price >= 2200: score -= 5
    if year is None:
        score -= 9
    elif year >= 2012: score += 11
    elif year >= 2008: score += 7
    elif year <= 2004: score -= 15
    elif year <= 2007: score -= 7
    if mileage is None: score -= 13
    elif mileage <= 130000: score += 13
    elif mileage <= 210000: score += 8
    elif mileage <= 260000: score += 1
    elif mileage >= 300000: score -= 17
    else: score -= 8
    if seller == "particulier": score += 13
    else: score -= 11
    if photos: score += 5
    else: score -= 7
    if posted is None: score -= 7
    elif now-posted <= timedelta(days=7): score += 6
    elif now-posted > timedelta(days=30): score -= 6
    if not location: score -= 5
    elif any(has_word(str(location).casefold(),place) for place in LOCAL_HINTS):
        score += 5
    # Do not trust seller-side arbitrary provider scores.
    # Older cars with premium asking prices are poor default trade leads.
    if year is not None and year <= 2007 and price >= 2100:
        score -= 24
        signals.append("Ouder bouwjaar met hoge vraagprijs: kritisch vergelijken")
    if year is not None and year <= 2003 and price > 1400:
        score -= 12
        signals.append("Hoge vraagprijs voor vroege generatie: controle vereist")
    desc = str(description or "").casefold()
    if any(word in desc for word in TECHNICAL_WARNINGS):
        score = min(score, 24)
        signals.append("Beschrijving vermeldt mogelijk technisch risico")
    return max(0,min(100,score)),signals

def data(file):
    try:return json.loads((ROOT/file).read_text(encoding="utf-8"))
    except (OSError,ValueError,TypeError):return None
def when(value):
    if not isinstance(value,str):return None
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        return dt if dt.tzinfo else None
    except (ValueError,OverflowError):return None
def money(value):
    """Apply the same Dutch-price parser used by Marktplaats research."""
    if isinstance(value, dict):
        value = value.get("amount", value.get("value"))
    from scanner import parse_price_eur
    amount = parse_price_eur(value)
    if amount is None or not 350 <= amount <= 3000:
        return None
    return round(amount, 2)
def normalize(raw,source,now,provider_at=None):
    if not isinstance(raw,dict):return None
    url=raw.get("url")
    if not isinstance(url,str):return None
    try:parsed=urlsplit(url)
    except ValueError:return None
    if (parsed.scheme!="https" or parsed.hostname not in ("www.facebook.com","facebook.com","m.facebook.com")
        or parsed.username or parsed.password):return None
    match=ID_RE.fullmatch(parsed.path)
    if not match:return None
    item_id=match.group(1)
    name=str(raw.get("title") or "").strip()
    if not is_vehicle_title(name) or not is_trade_model(name):return None
    title=name.casefold()
    if any(word in title for word in BLOCKED):return None
    if raw.get("severe_technical_risk"):return None
    price=money(raw.get("price"))
    if price is None:return None
    currency = str(raw.get("currency") or "").upper()
    formatted = str(raw.get("priceFormatted") or "").lstrip()
    if currency and currency != "EUR":return None
    if formatted.startswith(("$","£")):return None
    # Provider fields can disagree. Never let a lower kilometre field mask a
    # higher valid reading or let two materially inconsistent readings be
    # presented as one confirmed odometer. Do not request any extra data.
    raw_mileages = [parse_mileage(raw.get(key))
                    for key in ("mileage_km", "mileage")
                    if raw.get(key) is not None]
    mileages = [value for value in raw_mileages if value is not None]
    if any(value > 330000 for value in mileages):return None
    if len(mileages) > 1 and (
            max(mileages)-min(mileages) >
            max(1000, round(max(mileages)*.01))):return None
    mileage=mileages[0] if mileages else None
    posted=when(raw.get("listedAt") or raw.get("listed_at"))
    # If date is missing, do not invent one: keep a visible warning.
    if posted and (posted>now+timedelta(hours=3) or now-posted>MAX_AGE):return None
    # Distinguish new observations from the first time this listing was seen.
    # Old saved rows without checked_at stay conservatively historical.
    observed=(when(raw.get("checked_at")) or
              when(raw.get("first_seen_at")) or provider_at)
    if not observed or observed>now+timedelta(hours=3) or now-observed>MAX_AGE:return None
    # Normalized and older provider payloads use several dealer aliases.
    # Business evidence always overrides a conflicting private flag.
    seller_fields={str(raw.get(field) or "").strip().casefold()
                   for field in ("seller_type","seller_platform_type","sellerType")}
    if seller_fields & {
        "handelaar","zakelijk","business","business_seller","dealer",
        "dealership","professional","trader","company",
        "commercial","autobedrijf","garagebedrijf"
    }:return None
    seller=("particulier" if seller_fields & {
        "particulier","private","private_seller","individual","consumer"
    } else "onbekend")
    location=raw.get("city") or raw.get("location")
    if isinstance(location,dict):location=location.get("displayName") or location.get("city")
    location=str(location or "")[:100]
    year=raw.get("year_detected") or raw.get("year")
    if not isinstance(year,int) or isinstance(year,bool) or not 1950<=year<=now.year+1:
        found=re.search(r"\b(19[89]\d|20[012]\d)\b",title)
        year=int(found.group(1)) if found else None
    photos=provider_photos(raw)
    score, trade_warnings = trading_score(
        name, price, year, mileage, seller, photos, posted,
        location, raw.get("description"), now)
    remarks=[]
    if mileage is None:remarks.append("Kilometerstand niet geverifieerd")
    if year is None:remarks.append("Bouwjaar niet vastgesteld")
    if seller!="particulier":remarks.append("Privéverkoper nog niet bevestigd")
    if not posted:remarks.append("Advertentiedatum onbekend")
    if not location:remarks.append("Locatie/afstand niet bevestigd")
    else:remarks.append("Afstand vanaf Dronten nog niet geverifieerd")
    remarks.extend(trade_warnings)
    minimum_resale = price + 500 + 300 + (150 if mileage is None or mileage >= 250000 else 0)
    remarks.append(
        f"Voor EUR 500 handelsdoel minimaal EUR {minimum_resale:,.0f} nodig "
        "inclusief onderzoeksreserve; geen geverifieerde marktwaarde".replace(",", "."))
    remarks.append("Advertentieprijs, technische staat en marktwaarde niet onafhankelijk geverifieerd")
    return {"id":"facebook:"+item_id,"listing_id":item_id,"source":"facebook",
        "source_provider":source,"url":"https://www.facebook.com/marketplace/item/"+item_id+"/",
        "title":name[:200],"year":year,"mileage_km":mileage,
        "location_label":location,"seller_classification":seller if seller=="particulier" else "onbekend",
        "category":"facebook_onderzoek","score":score,
        "price_type":"provider_price_unverified","asking_price_eur":None,
        "minimum_bid_eur":None,"search_card_hint_eur":price,
        "median_comparable_asking_price_eur":None,"indicative_resale_eur":None,
        "comparable_count":0,"confirmed_profit_eur":None,"needs_inspection":True,
        "issues":remarks,"photo_urls":photos,
        "posted_at":posted.isoformat() if posted else None,
        "checked_at":observed.isoformat(),"availability":"not_confirmed"}
def find_candidates(now=None,limit=30,include_manual=False):
    now=now or datetime.now(timezone.utc)
    status=[];result={}
    for source,status_name,provider in SOURCES:
        details=data(status_name)
        records=data(source)
        checked=when(details.get("checked_at")) if isinstance(details,dict) else None
        status.append({"provider":provider,"scan_status":details.get("status") if isinstance(details,dict) else "missing",
                       "checked_at":checked.isoformat() if checked else None,
                       "saved":len(records) if isinstance(records,list) else 0})
        if not isinstance(records,list):continue
        for raw in records:
            item=normalize(raw,provider,now,provider_at=checked)
            if not item:continue
            prior=result.get(item["id"])
            # Prefer verified timestamp and actual additional structured fields.
            rank=(item["mileage_km"] is not None,item["year"] is not None,bool(item["photo_urls"]),item["checked_at"])
            old=(prior["mileage_km"] is not None,prior["year"] is not None,bool(prior["photo_urls"]),prior["checked_at"]) if prior else None
            if prior is None or rank>old:result[item["id"]]=item
    if include_manual:
        # Live app-local manual inbox only. It is not shared back to GitHub.
        from facebook_inbox import load_items
        for raw in load_items():
            normalized={**raw,"first_seen_at":raw.get("added_at")}
            item=normalize(normalized,"Handmatig",now)
            if item and item["id"] not in result:result[item["id"]]=item
    ordered=sorted(result.values(),key=lambda x:(
        -x["score"],
        x["seller_classification"]!="particulier",
        x["mileage_km"] is None,
        not bool(x["photo_urls"]),
        -int(x["year"] or 0),
        x["search_card_hint_eur"],x["id"]))
    return {"source":"facebook","count":min(len(ordered),limit),"candidates":ordered[:limit],
            "sources":status,"checked_at":max((s["checked_at"] for s in status if s["checked_at"]),default=None)}
def revision(items):
    return hashlib.sha256(json.dumps(sorted((x["id"],x["content_hash"]) for x in items),
                            separators=(",",":")).encode("utf-8")).hexdigest()
def for_pro(rows,archived=False):
    output=[]
    for row in rows:
        item={k:v for k,v in row.items() if k not in ("score","source_provider")}
        item["availability"]="historical_unverified" if archived else item["availability"]
        item["content_hash"]=hashlib.sha256(json.dumps(item,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode("utf-8")).hexdigest()
        output.append(item)
    return output
