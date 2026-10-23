"""Transparent preliminary resale triage, not a market valuation."""
import re

PREFERRED = ("aygo", "yaris", "corsa", "polo", "golf", "clio", "fiesta",
             "focus", "picanto", "alto", "swift", "punto", "107", "108",
             "208", "206", "i10", "i20", "fabia", "ibiza", "astra")
HIGH_RISK = ("motorprobleem", "motor problemen", "motorproblemen", "motorschade", "motor defect", "motor kapot", "motor stuk", "koppakking", "cilinderkop defect", "versnellingsbak kapot", "versnellingsbak stuk", "start niet", "rijdt niet", "oververhit", "water in olie", "versnellingsbak defect", "transmission problem", "gearbox problem", "automatic transmission problem", "niet rijdend", "loopt niet", "loopt slecht", "loopt niet goed", "motor loopt slecht", "motor loopt onregelmatig", "vermoedelijk koppakking", "waarschijnlijk koppakking", "koelvloeistof in olie", "olie in koelvloeistof", "distributieriem gebroken", "geen papieren", "schadeauto", "schade auto")
WARNINGS = ("zonder apk", "geen apk", "motorprobleem", "motorproblemen", "motor defect", "motorschade",
            "koppakking", "versnellingsbak defect", "niet rijdend",
            "loopt niet", "transmission problem", "gearbox problem", "schadeauto", "schade auto", "motorstoring",
            "distributieriem gebroken", "geen papieren", "project")
POSITIVE = ("nieuwe apk", "apk nieuw", "recent apk", "onderhoudsboek",
            "onderhouden", "2 sleutels", "airco", "nap")

def target_profit(purchase_price):
    """Minimum target net trading profit for A.E.T CARS: EUR 500 per car."""
    return 500.0

def target_resale_price(purchase_price, estimated_costs=0):
    """Break-even plus desired profit; not a market-value prediction."""
    return round(purchase_price + estimated_costs + target_profit(purchase_price), 2)


def assess(item):
    title = str(item.get("title") or "").lower()
    price = item.get("price")
    if not isinstance(price, (int, float)):
        return {"score": 0, "priority": "onbekend", "reasons": ["Prijs ontbreekt"]}
    score = 35
    quick_sale = any(re.search(r"(?<!\w)" + re.escape(model) + r"(?!\w)", title) for model in PREFERRED)
    high_risk = any(term in (title + " " + str(item.get("description") or "").lower()) for term in HIGH_RISK)
    reasons = []
    if any(re.search(r"(?<!\w)" + re.escape(model) + r"(?!\w)", title) for model in PREFERRED):
        score += 18
        reasons.append("Gewild betaalbaar model (indicatie)")
    if 500 <= price <= 1800:
        score += 15
        reasons.append("Lage aanschafprijs")
    elif price > 2500:
        score -= 8
    # Prefer structured vehicle year; an APK expiry year in the title is not a model year.
    year = item.get('year_detected') or item.get('year')
    if isinstance(year, str):
        year = int(year.strip()) if re.fullmatch(r'(?:19|20)\d{2}', year.strip()) else None
    if not isinstance(year, int) or not 1950 <= year <= 2030:
        title_without_apk = re.sub(r'(?i)\bapk\b[^,;|\n]{0,45}', ' ', title)
        year_match = re.search(r'\b(19[89]\d|20[012]\d)\b', title_without_apk)
        year = int(year_match.group(1)) if year_match else None
    if year and year >= 2010:
        score += 20
        reasons.append("Bouwjaar vanaf 2010 (advertentiegegevens of titel)")
    elif year and year < 2005:
        score -= 15
        reasons.append("Ouder bouwjaar")
    if any(term in (title + " " + str(item.get("description") or "").lower()) for term in WARNINGS):
        score -= 35
        reasons.append("Mogelijk technisch of juridisch risico")
    if any(term in title for term in POSITIVE):
        score += 8
        reasons.append("Positieve advertentieclaim (niet gecontroleerd)")
    if high_risk:
        score = min(score, 20)
        reasons.append("Ernstig risico: geen snelle doorverkoop zonder onderzoek")
    if quick_sale and not high_risk:
        score += 7
        reasons.append("Extra voorkeur: snelle doorverkoop")
    score = max(0, min(100, score))
    desired_profit = target_profit(price)
    minimum_resale = target_resale_price(price)
    return {"target_profit_eur": desired_profit,
            "minimum_resale_before_costs_eur": minimum_resale,
            "score": score, "priority": "bekijken" if score >= 65 and not high_risk else "normaal" if score >= 45 and not high_risk else "laag",
            "year_from_title": year, "reasons": reasons,
            "valuation_verified": False, "profit_estimate_eur": None}

def extra_checks(item):
    """Only infer what is explicitly present in the listing; unknown remains unknown."""
    text = (str(item.get("title") or "") + " " + str(item.get("description") or "")).lower()
    mileage = item.get("mileage")
    if isinstance(mileage, str):
        cleaned = re.sub(r"[^0-9]", "", mileage)
        mileage = int(cleaned) if cleaned else None
    if not isinstance(mileage, (int, float)):
        m = re.search(r"\b(\d{1,3}(?:[.,]\d{3})+|\d{4,6})\s*(?:km|kilometer)\b", text)
        mileage = int(re.sub(r"\D", "", m.group(1))) if m else None
    year = item.get("year")
    if isinstance(year, str) and re.fullmatch(r"(?:19|20)\d{2}", year.strip()):
        year = int(year.strip())
    if not isinstance(year, int):
        # APK expiry dates must never masquerade as vehicle model years.
        year_text = re.sub(r'\bapk\b[^,;|\n]{0,45}', ' ', text)
        m = re.search(r'\b(19[89]\d|20[012]\d)\b', year_text)
        year = int(m.group(1)) if m else None
    apk = ("ongunstig" if any(t in text for t in ("geen apk", "zonder apk", "apk verlopen", "apk afgekeurd"))
           else "positieve_claim_onbevestigd" if any(t in text for t in ("nieuwe apk", "apk nieuw", "apk tot", "apk geldig"))
           else "onbekend")
    city = str(item.get("city") or "").strip().lower()
    near = {"dronten", "swifterbant", "biddinghuizen", "lelystad", "kampen", "elburg", "harderwijk", "zeewolde", "nunspeet", "zwolle", "almere", "almere-stad", "urk", "emmeloord", "apeldoorn", "amersfoort", "nijkerk", "putten", "ermelo"}
    far = {"groningen", "leeuwarden", "maastricht", "eindhoven", "tilburg", "breda", "rotterdam", "den haag", "enschede", "venlo"}
    distance = "waarschijnlijk_binnen_100km_controleren" if city in near else "mogelijk_verder_dan_100km" if city in far else "onbekend"
    return {"year_detected": year, "mileage_km": mileage, "apk_status": apk, "distance_from_dronten": distance}

def vehicle_identity(item):
    """Require exact model tokens and distinguish major variants where recognizable."""
    title = str(item.get("title") or "").lower()
    models = ("space star", "punto evo", "grande punto", "polo", "golf", "corsa",
              "clio", "fiesta", "focus", "aygo", "yaris", "picanto", "alto",
              "swift", "punto", "fabia", "ibiza", "astra", "twingo", "panda",
              "micra", "c1", "c2", "c3", "107", "108", "206", "207", "208",
              "i10", "i20", "500", "roomster", "scenic", "modus", "meriva",
              "agila", "ka", "fusion", "getz", "matrix", "ceed", "rio",
              "splash", "saxo", "xsara", "berlingo", "partner", "touran")
    model = next((m for m in models if re.search(r"(?<!\w)" + re.escape(m) + r"(?!\w)", title)), None)
    engine = re.search(r"(?<!\d)([0-3][.,][0-9])\s*(?:tsi|tfsi|ecoboost|twinair|hdi|tdi|dci|vti|16v|8v)?\b", title)
    engine = engine.group(1).replace(",", ".") if engine else None
    fuel = ("diesel" if re.search(r"\b(diesel|tdi|hdi|dci|cdti|d-4d)\b", title)
            else "elektrisch" if re.search(r"\b(electric|elektrisch|ev)\b", title)
            else "hybride" if re.search(r"\b(hybrid|hybride|phev|gte)\b", title)
            else None)
    body = "wagon" if re.search(r"\b(wagon|station|estate|sw|break)\b", title) else None
    transmission = ("automaat" if re.search(r"\b(automaat|automatic|dsg|cvt)\b", title)
                    else "handgeschakeld" if re.search(r"\b(handgeschakeld|manual)\b", title) else None)
    return {"model": model, "engine_l": engine, "fuel": fuel, "body": body, "transmission": transmission}


def market_comparison(item, reference_path="marktplaats_vergelijkingen.json"):
    """Fresh independent asking prices for same model, similar year/km and known variants."""
    import json
    from pathlib import Path
    from statistics import median
    from datetime import datetime, timezone, timedelta
    identity = vehicle_identity(item)
    model = identity["model"]
    year = item.get("year_detected") or item.get("year")
    km = item.get("mileage_km") or item.get("mileage")
    if not model:
        return {"status": "model_onbekend", "sample_size": 0}
    if not isinstance(year, int) or not isinstance(km, (int, float)):
        return {"status": "onvoldoende_voertuiggegevens", "sample_size": 0, "model": model}
    try:
        rows = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rows = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=21)
    matches = []
    seen = set()
    excluded_variant_conflicts = 0
    excluded_stale = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        # A monthly lease instalment is not a comparable purchase price.
        row_title = str(row.get("title") or "").lower()
        row_description = str(row.get("description") or "").lower()
        row_price = row.get("price")
        if any(t in row_title for t in ("financial lease", "private lease", "per maand", "p/m", "maandbedrag")):
            continue
        if isinstance(row_price, (int, float)):
            amount = str(int(row_price))
            if re.search(r"(?:€\s*)?" + re.escape(amount) + r"\s*(?:p/m|per maand|/mnd)", row_description):
                continue
        # Only verified full descriptions are trusted as clean retail references.
        if row.get("description_source") == "meta_samenvatting":
            continue
        # Broken cars and non-running projects must not depress the normal
        # retail asking-price reference for roadworthy vehicles.
        if any(term in (str(row.get('title') or '') + ' ' + str(row.get('description') or '')).lower() for term in HIGH_RISK):
            continue
        other = vehicle_identity(row)
        if other["model"] != model:
            continue
        # Do not mix known conflicting engine, fuel, body or transmission types.
        if any(identity[k] and other[k] and identity[k] != other[k]
               for k in ("engine_l", "fuel", "body", "transmission")):
            excluded_variant_conflicts += 1
            continue
        try:
            timestamp = datetime.fromisoformat(str(row.get("checked_at")).replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp < cutoff:
                excluded_stale += 1
                continue
        except (ValueError, TypeError):
            continue
        price, y, k, url = (row.get("price"), row.get("year_detected") or row.get("year"),
                            row.get("mileage_km") or row.get("mileage"), row.get("url"))
        if isinstance(y, str) and re.fullmatch(r"(?:19|20)\d{2}", y.strip()):
            y = int(y.strip())
        if isinstance(k, str):
            digits = re.sub(r"[^0-9]", "", k)
            k = int(digits) if digits else None
        if not isinstance(price, (int, float)) or not 350 <= price <= 12000:
            continue
        if not isinstance(y, int) or abs(y - year) > 2:
            continue
        if not isinstance(k, (int, float)) or abs(k - km) > 50000:
            continue
        if not url or url == item.get("url") or url in seen:
            continue
        seen.add(url)
        matches.append((abs(y - year) * 20000 + abs(k - km), price, url))
    matches.sort(key=lambda x: x[0])
    # Prefer the closest vehicles, avoiding unrelated extremes.
    closest = matches[:12]
    if len(closest) < 3:
        return {"status": "onvoldoende_vergelijkingen", "sample_size": len(closest),
                "model": model, "criteria": "bouwjaar ±2; kilometerstand ±50.000; geen bekende variantconflicten"}
    prices = sorted(x[1] for x in closest)
    # Extreme spread signals that apparently similar ads may differ in condition.
    # Report the uncertainty rather than treating the median as a sale price.
    price_spread_ratio = round(prices[-1] / prices[0], 2) if prices[0] else None
    return {"status": "indicatieve_vraagprijzen", "sample_size": len(prices),
            "price_spread_ratio": price_spread_ratio,
            "high_price_dispersion": bool(price_spread_ratio and price_spread_ratio >= 2.5),
            "excluded_variant_conflicts": excluded_variant_conflicts,
            "excluded_stale": excluded_stale,
            "model": model, "vehicle_identity": identity,
            "median_asking_price_eur": round(median(prices)),
            "lowest_asking_price_eur": round(prices[0]),
            "highest_asking_price_eur": round(prices[-1]),
            "criteria": "bouwjaar ±2; kilometerstand ±50.000; geen bekende variantconflicten",
            "verified_sale_prices": False}



def deal_watchlist(item, rating, comparison):
    """Transparent review signal, never a confirmed profit or completed sale."""
    if rating.get("severe_technical_risk") or any(term in (str(item.get("title") or "") + " " + str(item.get("description") or "")).lower() for term in HIGH_RISK) or not isinstance(item.get("price"), (int, float)):
        return {"watchlist_tier": "uitsluiten", "watchlist_reason": "Technisch risico of onbekende prijs"}
    if item["price"] > 3000:
        return {"watchlist_tier": "buiten_budget", "watchlist_reason": "Boven inkoopbudget"}
    # Price gaps are only meaningful when the listing identity is sufficiently known.
    year = item.get("year_detected") or item.get("year")
    mileage = item.get("mileage_km") or item.get("mileage")
    if isinstance(year, str):
        year = int(year.strip()) if re.fullmatch(r'(?:19|20)\d{2}', year.strip()) else None
    if isinstance(mileage, str):
        digits = re.sub(r'\D', '', mileage)
        mileage = int(digits) if digits else None
    if not isinstance(year, int) or not isinstance(mileage, (int, float)):
        if isinstance(year, int) and year < 2010 and item["price"] <= 1250:
            return {"watchlist_tier": "goedkope_oudere_auto_controleren", "watchlist_reason": "Goedkope oudere auto; kilometerstand controleren, winst onbekend"}
        return {"watchlist_tier": "voertuiggegevens_controleren",
                "watchlist_reason": "Bouwjaar of kilometerstand ontbreekt"}
    # Older cars can still be excellent buys, particularly after negotiation.
    # Flag low-cost older cars for manual negotiation review even when
    # there are too few matching adverts to calculate a defensible median.
    if year < 2010 and item["price"] <= 1250 and not rating.get("severe_technical_risk"):
        if comparison.get("status") != "indicatieve_vraagprijzen":
            return {"watchlist_tier": "goedkope_oudere_auto_controleren",
                    "watchlist_reason": "Goedkope oudere auto: inspectie en onderhandelen; winst onbekend",
                    "reference_sample_size": comparison.get("sample_size", 0)}
    sample = comparison.get("sample_size", 0)
    median_price = comparison.get("median_asking_price_eur")
    if isinstance(median_price, (int, float)) and sample >= 3:
        gap = round(median_price - item["price"])
        discount = round(100 * gap / median_price) if median_price > 0 else 0
        tier = ("sterke_prijsafwijking_controleren" if discount >= 25 and gap >= 500
                else "prijsafwijking_controleren" if discount >= 15 and gap >= 300
                else "geen_duidelijke_prijsafwijking")
        return {"watchlist_tier": tier, "watchlist_reason": "Verschil met mediane vraagprijs, geen verkoopprijs",
                "asking_price_gap_eur": gap, "asking_price_discount_pct": discount,
                "reference_sample_size": sample}
    return {"watchlist_tier": "handmatig_prijsonderzoek",
            "watchlist_reason": "Te weinig vergelijkbare vraagprijzen voor een prijsafwijking",
            "reference_sample_size": sample}

def estimated_trade_costs(item, details):
    """Conservative planning reserve, not a repair quote."""
    text = (str(item.get("title") or "") + " " + str(item.get("description") or "")).lower()
    reserve = 250
    reasons = ["EUR 250 basisreserve voor transport, voorbereiding en kleine gebreken"]
    if details.get("apk_status") == "ongunstig":
        reserve += 400
        reasons.append("EUR 400 extra voor ontbrekende/verlopen APK en mogelijke herstelkosten")
    elif details.get("apk_status") == "onbekend":
        reserve += 150
        reasons.append("EUR 150 extra omdat APK-status onbekend is")
    mileage = details.get("mileage_km")
    if isinstance(mileage, (int, float)) and mileage >= 250000:
        reserve += 150
        reasons.append("EUR 150 extra slijtagebuffer bij hoge kilometerstand")
    if any(term in text for term in ("distributie onbekend", "distributieriem onbekend", "onderhoud onbekend")):
        reserve += 200
        reasons.append("EUR 200 extra voor onbekend onderhoud")
    return reserve, reasons


def quality_gate(item, base, details, comparison):
    """Conservative lead triage: attractive score alone cannot imply a validated deal."""
    score = int(base["score"])
    severe_risk = any(term in (str(item.get("title") or "") + " " + str(item.get("description") or "")).lower() for term in HIGH_RISK)
    reasons = list(base["reasons"])
    missing = []
    for field, value in (("bouwjaar", details["year_detected"]), ("kilometerstand", details["mileage_km"])):
        if value is None:
            missing.append(field)
    if not str(item.get("description") or "").strip():
        missing.append("advertentiebeschrijving")
    elif item.get("description_source") not in ("jsonld", "pagina_tekst") or len(str(item.get("description") or "").strip()) < 180:
        missing.append("volledige advertentiebeschrijving")
    if details["apk_status"] == "onbekend":
        missing.append("APK")
    if details["distance_from_dronten"] == "onbekend":
        missing.append("afstand")
    if missing:
        score -= min(25, 6 * len(missing))
        reasons.append("Ontbrekende gegevens: " + ", ".join(missing))
    if details["apk_status"] == "ongunstig":
        score -= 20
    if details["distance_from_dronten"] == "mogelijk_verder_dan_100km":
        score -= 15
    # High mileage is not a rejection by itself: compare against similarly driven cars.
    comparable = comparison.get("status") == "indicatieve_vraagprijzen"
    uncertain_market = bool(comparison.get("high_price_dispersion"))
    if uncertain_market:
        reasons.append("Grote spreiding in vergelijkbare vraagprijzen: marktwaarde extra controleren")
    if comparable:
        # Conservative 15% negotiation/repair allowance on asking prices.
        resale_reference = round(comparison["median_asking_price_eur"] * 0.85)
        costs_allowance, cost_reasons = estimated_trade_costs(item, details)
        margin = resale_reference - item["price"] - costs_allowance
        max_bid = max(0, resale_reference - costs_allowance - base["target_profit_eur"])
        negotiation_gap = max(0, item["price"] - max_bid)
        reasons.append(f"Indicatieve verkoopprijs: 85% van vergelijkbare vraagprijzen; minimaal EUR {base['target_profit_eur']:g} winst")
        reasons.extend(cost_reasons)
        if margin < 500:
            score -= 20
    else:
        margin = None
        max_bid = None
        negotiation_gap = None
        costs_allowance = None
        reasons.append("Geen voldoende onderbouwde marktvergelijking; maximale biedprijs onbekend")
    score = max(0, min(100, score))
    # Only surface high-priority leads when essential fields and comparable prices exist.
    actionable = (not severe_risk and not missing and comparable and not uncertain_market and margin is not None
                  and margin >= base["target_profit_eur"]
                  and details["apk_status"] != "ongunstig"
                  and score >= 70)
    priority = "technisch_risico" if severe_risk else "bekijken" if actionable else "nader_onderzoek" if score >= 45 else "laag"
    return {**base, "score": score, "priority": priority,
            "reasons": reasons, "severe_technical_risk": severe_risk, "missing_information": missing,
            "actionable_lead": actionable, "indicative_margin_eur": margin,
            "target_net_profit_eur": base["target_profit_eur"],
            "indicative_resale_eur": resale_reference if comparable else None,
            "max_purchase_price_eur": max_bid,
            "required_negotiation_eur": negotiation_gap,
            "asking_price_within_buy_limit": (item["price"] <= max_bid) if max_bid is not None else None,
            "estimated_costs_allowance_eur": costs_allowance,
            "valuation_verified": False, "profit_estimate_eur": None}
