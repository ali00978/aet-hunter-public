"""App-ready Hunter snapshot from the already saved Marktplaats scans.

No web traffic, no background scraper, no unauthenticated data publication.
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from marktplaats_potential import select_potential_candidates

ROOT = Path(os.environ.get("AET_HUNTER_STATE_DIR", Path(__file__).resolve().parent))
RESEARCH = 'marktplaats_proefresultaten.json'
SEARCH = 'marktplaats_handmatig_controleren.json'
STATUS = 'marktplaats_status.json'
SNAPSHOT = 'hunter_feed.json'

def _json(name):
    try:
        return json.loads((ROOT / name).read_text(encoding='utf-8'))
    except (ValueError, OSError, TypeError):
        return None

def _time(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return stamp if stamp.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None

def build(now=None, max_price=3000):
    now = now or datetime.now(timezone.utc)
    one, two, state = _json(RESEARCH), _json(SEARCH), _json(STATUS)
    research = one if isinstance(one, list) else []
    search = two if isinstance(two, list) else []
    state = state if isinstance(state, dict) else {}
    sources = {}
    for name, data in ((RESEARCH, one), (SEARCH, two)):
        sources[name] = {'exists': (ROOT / name).exists(),
                         'count': len(data) if isinstance(data, list) else 0,
                         'valid': isinstance(data, list)}
    current = select_potential_candidates(research, search, max_price=max_price,
                                          limit=30, now=now)
    archived = False
    checked = _time(state.get('checked_at'))
    if not current:
        # Last-known history remains inspectable, clearly labelled OLD.
        stamps = [checked]
        stamps.extend(_time(r.get('checked_at') or r.get('discovered_at'))
                      for r in research + search if isinstance(r, dict))
        latest = max((s for s in stamps if s is not None), default=None)
        if latest is not None:
            current = select_potential_candidates(
                research, search, max_price=max_price, limit=30,
                now=latest + timedelta(minutes=1))
            archived = bool(current)
    # No values are recomputed: the scanner's margins and strict shortlist
    # remain separate; the UI must not treat this broad list as purchase advice.
    allowed = ('url','title','description','source','price','asking_price_eur',
               'image_url','image_urls',
               'minimum_bid_eur','price_type','seller_type','seller_name',
               'location','year','year_detected','mileage','mileage_km',
               'checked_at','discovered_at','apk_status','risk_category',
               'repair_review_required','description_source',
               'market_comparison','comparison_sample_size',
               'indicative_resale_eur','estimated_costs_allowance_eur',
               'potential_rank','potential_category','potential_category_label',
               'potential_display_price','potential_notes',
               'potential_checked_at')
    return {'version':1,'generated_at':now.isoformat(),
            'scan_checked_at':state.get('checked_at'),
            'scan_health':state.get('scan_health','onbekend'),
            'source_info':sources,'archived':archived,
            'count':len(current),
            'cars':[{k:r[k] for k in allowed if k in r} for r in current]}

def snapshot():
    result = build()
    target = ROOT / SNAPSHOT
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w',dir=ROOT,delete=False,
                                         prefix='hunter_',suffix='.tmp',
                                         encoding='utf-8') as handle:
            tmp = Path(handle.name)
            json.dump(result,handle,ensure_ascii=False,indent=2,allow_nan=False)
        os.replace(tmp,target)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return result

def read(now=None,max_price=3000):
    now = now or datetime.now(timezone.utc)
    state = _json(STATUS)
    saved = _json(SNAPSHOT) if max_price == 3000 else None
    if isinstance(saved,dict) and saved.get('version')==1 and isinstance(saved.get('cars'),list):
        generated = _time(saved.get('generated_at'))
        scan_at = state.get('checked_at') if isinstance(state,dict) else None
        if (generated is not None and timedelta(0) <= now-generated <= timedelta(minutes=15)
                and saved.get('scan_checked_at') == scan_at):
            return saved
    return build(now=now,max_price=max_price)

if __name__ == '__main__':
    info=snapshot()
    print(f"App Hunter feed: {info['count']} / 30 cars; archived={info['archived']}; scan={info['scan_checked_at']}")
