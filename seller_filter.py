"""Conservative seller-type inference; unknown sellers are never silently treated as private."""
import re

BUSINESS_TERMS = ("autobedrijf", "autohandel", "automotive", "garagebedrijf",
                  "autoservice", "auto service", "occasioncentrum", "occasion center",
                  "bv", "b.v.", "handelsonderneming")
PRIVATE_TERMS = ("particulier", "private", "individual")

def seller_type(item):
    explicit = str(item.get("seller_type") or item.get("sellerType") or "").lower()
    if explicit in ("business", "dealer", "professional", "company", "zakelijk"):
        return "handelaar"
    if explicit in ("private", "individual", "particulier"):
        return "particulier"
    if item.get("seller_is_business") is True or item.get("isBusiness") is True:
        return "handelaar"
    if item.get("seller_is_business") is False or item.get("isBusiness") is False:
        return "particulier"
    seller = str(item.get("seller_name") or item.get("sellerName") or "").lower()
    if any(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", seller) for t in BUSINESS_TERMS):
        return "handelaar"
    return "onbekend"

def seller_priority(item, seller):
    """Business listings qualify only with unusually strong *indicative* evidence."""
    kind = seller_type(seller)
    comparison = item.get("market_comparison") or {}
    margin = item.get("indicative_margin_eur")
    target = item.get("target_profit_eur")
    exceptional = (kind == "handelaar" and
                   comparison.get("status") == "indicatieve_vraagprijzen" and
                   isinstance(margin, (int, float)) and isinstance(target, (int, float)) and
                   margin >= target + 500 and
                   item.get("score", 0) >= 70 and
                   item.get("apk_status") != "ongunstig")
    return {"seller_type": kind, "seller_type_verified": kind != "onbekend",
            "exceptional_dealer_deal": bool(exceptional),
            "seller_review_priority": ("uitzonderlijke_handelaar_controleren" if exceptional else
                                       "handelaar_lage_prioriteit" if kind == "handelaar" else
                                       "particulier_voorrang" if kind == "particulier" else
                                       "verkoper_onbekend")}
