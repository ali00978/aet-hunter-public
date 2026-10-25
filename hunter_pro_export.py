"""Private-repository, offline AET PRO import format; never sends credentials or HTTP."""
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from marktplaats_data import parse_mileage, photo_urls

FORMAT="aet-pro.hunter.v1"
DEST=Path(os.environ.get("AET_HUNTER_STATE_DIR", Path(__file__).resolve().parent))/"hunter_pro_export.json"
ID=re.compile(r"/v/auto-s/[^/]+/([ma]\d+)(?:-[^/]*)?")
def amount(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and 0<value<=1_000_000
def date(value):
    try:
        moment=datetime.fromisoformat(value.replace("Z","+00:00"))
        return moment.isoformat() if moment.tzinfo else None
    except (ValueError,AttributeError,OverflowError):return None
def canonical(url):
    if not isinstance(url,str):return None
    try:
        parsed=urlsplit(url)
        if parsed.scheme!="https" or parsed.hostname not in ("www.marktplaats.nl","marktplaats.nl") or parsed.username or parsed.password or parsed.port not in (None,443):return None
    except ValueError:return None
    path=parsed.path.rstrip("/")
    found=ID.fullmatch(path)
    return (found.group(1),"https://www.marktplaats.nl"+path) if found else None
def candidate(row,archive=False):
    if not isinstance(row,dict) or row.get("reserved") or row.get("unavailable") or row.get("severe_technical_risk"):return None
    locator=canonical(row.get("url"))
    if not locator:return None
    km=parse_mileage(row.get("mileage_km",row.get("mileage")))
    if km is not None and km>330000:return None
    seller=row.get("seller_type")
    if seller not in ("particulier","onbekend"):return None
    kind=row.get("price_type")
    search=row.get("source")=="marktplaats_search_card" or row.get("potential_category")=="zoekkaart"
    price=row.get("price")
    if kind=="FIXED" and not search and (not amount(price) or price>3000):return None
    if search and (not amount(price) or price>3000):return None
    floor=row.get("minimum_bid_eur")
    if kind=="MIN_BID" and floor is not None and (not amount(floor) or floor>3000):return None
    fixed=round(float(price),2) if kind=="FIXED" and not search and amount(price) else None
    checked=date(row.get("checked_at") or row.get("discovered_at"))
    if not checked:return None
    compar=row.get("market_comparison")
    compar=compar if isinstance(compar,dict) else {}
    sample=compar.get("sample_size")
    median=compar.get("median_asking_price_eur")
    credible=(compar.get("status")=="indicatieve_vraagprijzen" and isinstance(sample,int) and not isinstance(sample,bool) and sample>=3 and amount(median) and not compar.get("high_price_dispersion"))
    resale=row.get("indicative_resale_eur")
    issues=row.get("potential_notes")
    issues=issues if isinstance(issues,list) else []
    year=row.get("year") or row.get("year_detected")
    if not isinstance(year,int) or isinstance(year,bool) or not 1950<=year<=2030:year=None
    id_,url=locator
    result={
        "id":"marktplaats:"+id_,"source":"marktplaats","listing_id":id_,"url":url,
        "title":str(row.get("title") or "Auto")[:220],"year":year,"mileage_km":km,
        "seller_classification":seller,"location_label":str(row.get("location") or "")[:120],
        "category":str(row.get("potential_category") or "onderzoek")[:70],
        "rank":row.get("potential_rank") if isinstance(row.get("potential_rank"),int) else None,
        "price_type":"search_card_unverified" if search else "advertised_fixed" if fixed is not None else "minimum_bid" if kind=="MIN_BID" else "price_unknown",
        "asking_price_eur":fixed,
        "minimum_bid_eur":round(float(floor),2) if kind=="MIN_BID" and amount(floor) else None,
        "search_card_hint_eur":round(float(price),2) if search and amount(price) and price<=3000 else None,
        "median_comparable_asking_price_eur":round(float(median),2) if credible else None,
        "indicative_resale_eur":round(float(resale),2) if credible and amount(resale) else None,
        "comparable_count":sample if credible else 0,
        "confirmed_profit_eur":None,"needs_inspection":True,
        "issues":[s[:300] for s in issues if isinstance(s,str) and s.strip()][:12],
        "photo_urls":photo_urls(row.get("image_urls") or [row.get("image_url")])[:5],
        "checked_at":checked,"availability":"historical_unverified" if archive else "not_confirmed",
    }
    # A seller may change the Marktplaats URL slug without changing the car.
    # Keep the latest clickable URL but don't create a false update event.
    stable={k:v for k,v in result.items() if k not in ("rank","url")}
    result["content_hash"]=hashlib.sha256(json.dumps(stable,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    return result
def build_payload(feed, known_fingerprints=None, limit=30):
    """Choose up to 100 unique vehicles from at most 300 locally saved cars.
    Legacy standalone v1 exports stay capped at 30. No provider calls."""
    if not isinstance(feed,dict) or feed.get("version")!=1 or not isinstance(feed.get("cars"),list):
        raise ValueError("Onbekende of ongeldige Hunter-feed")
    if known_fingerprints is not None and not isinstance(known_fingerprints,dict):
        raise ValueError("Ongeldige privégeschiedenis voor selectie")
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError("Maximaal 100 onderzoeksadvertenties per bron")
    entries=[];seen=set()
    for row in feed["cars"][:300]:
        item=candidate(row,bool(feed.get("archived")))
        if item is not None and item["id"] not in seen:
            entries.append(item);seen.add(item["id"])
        if len(entries)>=limit and known_fingerprints is None:break
    if known_fingerprints is not None:
        # STRICT NEW-ONLY mode for private outbound Hunter batches. Once an
        # ID has been accepted, even its price drop is NOT a new advertisement.
        # The 30-card legacy research export stays unchanged (history=None).
        # Inspect all 300 saved rows first, otherwise repeated top-ranked cars
        # hide still-unseen candidates deeper in the existing offline pool.
        entries = [item for item in entries if item["id"] not in known_fingerprints]
    entries=entries[:limit]
    revision=hashlib.sha256(json.dumps(sorted((x["id"],x["content_hash"]) for x in entries),separators=(",",":")).encode()).hexdigest()
    return {"schema":FORMAT,"source":"aet-cars-ai-hunter","integration_mode":"offline_export_no_upload",
            "scan_checked_at":date(feed.get("scan_checked_at")),
            "generated_at":date(feed.get("generated_at")),
            "archived":bool(feed.get("archived")),"count":len(entries),
            "revision":revision,"candidates":entries}
def write_payload(target=DEST,feed=None):
    if feed is None:
        from hunter_feed import build
        feed=build()
    content=build_payload(feed)
    path=Path(target);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile("w",dir=path.parent,delete=False,prefix="pro-hunter-",suffix=".tmp",encoding="utf-8") as f:
            temporary=Path(f.name);json.dump(content,f,ensure_ascii=False,indent=2,allow_nan=False)
        os.replace(temporary,path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    return content
if __name__=="__main__":
    p=write_payload()
    print("AET PRO ready",p["count"],"candidates",p["schema"])
