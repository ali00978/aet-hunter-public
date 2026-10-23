"""Free-credit bounded Apify provider used by the standalone Hunter.

Never print a provider token, listing or raw response. One provider POST per
scheduled invocation; billable calls have a strict reservation, a remote
account-headroom check, and persisted cooldowns. A caller must durably store
the budget before the first POST.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

ACTOR = "hydedata~facebook-marketplace-scraper"
BASE = "https://api.apify.com/v2/acts/" + ACTOR + "/run-sync-get-dataset-items"
# Owner-approved profile: 3 starts/day * 31 days * $0.05 = $4.65.
# Previous $0.06 reservations are retained, not reset.
MAX_MONTHLY_STARTS = 93
MAX_ITEMS_PER_RUN = 80
FREE_MONTHLY_CREDIT_USD = 5.00
LOCAL_MONTHLY_RESERVE_USD = 4.65
DEFAULT_MAX_RUN_USD = 0.05
ACCOUNT_HEADROOM_CUSHION_USD = 0.30
COOLDOWN_FILE = "apify_backoff.json"
ONE_TIME_RETRY_FILE = "apify_retry_once.json"
ACCOUNT_LIMITS = "https://api.apify.com/v2/users/me/limits"
ACCOUNT_PLAN = "https://api.apify.com/v2/users/me"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent,
                prefix=".apify-", suffix=".tmp", delete=False, encoding="utf-8") as f:
            name = Path(f.name)
            json.dump(obj, f, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


def bounded_budget(env):
    """Keep provider calls within free credits even if old variables are high.

    Explicit AET_FACEBOOK_ENABLED=true is still required to initiate a request.
    A limit here is a *reserved worst case*, not a guaranteed Actor price.
    """
    def money(key, default):
        raw = env.get(key, "")
        if raw is None or raw == "":
            raw = default
        try:
            value = float(raw)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Invalid Apify budget configuration") from None
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Invalid Apify budget configuration")
        return value
    monthly = min(money("AET_APIFY_MONTHLY_BUDGET_USD", LOCAL_MONTHLY_RESERVE_USD),
                  LOCAL_MONTHLY_RESERVE_USD)
    per_run = min(money("AET_APIFY_MAX_CHARGE_USD", DEFAULT_MAX_RUN_USD),
                  DEFAULT_MAX_RUN_USD)
    # Stop rather than silently round a tiny positive budget down to zero.
    if round(monthly, 2) <= 0 or round(per_run, 2) <= 0:
        raise ValueError("Apify budget too small to safely reserve")
    return round(monthly, 2), round(per_run, 2)


def verify_free_headroom(token, cap, opener=None):
    """Allow one capped Actor only within VERIFIED included/prepaid usage.

    Apify account monthly limits can be higher than included plan credits.
    A $100 billing ceiling is NOT $100 of free credits. Read plan credits
    separately and require $0.30 below BOTH account limit and included
    credits, plus an absolute $4.65 cap against verified account usage. Never perform a paid POST if either GET fails.
    """
    if not isinstance(token, str) or len(token.strip()) < 15:
        raise ValueError("Missing Apify token")
    if type(cap) not in (int, float) or not 0 < cap <= DEFAULT_MAX_RUN_USD:
        raise ValueError("Apify test charge cap invalid")
    opener = opener or build_opener(NoRedirect())

    def account_get(url):
        req = Request(url, method="GET", headers={
            "Authorization": "Bearer " + token.strip(),
            "Accept": "application/json",
            "User-Agent": "AET-Hunter-Included-Credit-Guard/2",
        })
        try:
            with opener.open(req, timeout=12) as response:
                raw = response.read(60001)
                if response.status != 200 or len(raw) > 60000:
                    raise ValueError("apify_account_data_unknown")
            result = json.loads(raw.decode("utf-8"))
            data = result.get("data") if isinstance(result, dict) else None
            if not isinstance(data, dict):
                raise ValueError("apify_account_data_unknown")
            return data
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError,
                TypeError, KeyError, json.JSONDecodeError) as error:
            raise ValueError("apify_account_data_unknown") from error

    limits_data = account_get(ACCOUNT_LIMITS)
    limits = limits_data.get("limits")
    current = limits_data.get("current")
    maximum = limits.get("maxMonthlyUsageUsd") if isinstance(limits, dict) else None
    spent = current.get("monthlyUsageUsd") if isinstance(current, dict) else None
    if (type(maximum) not in (int, float) or type(spent) not in (int, float)
            or not math.isfinite(maximum) or not math.isfinite(spent)
            or spent < 0 or maximum <= 0):
        raise ValueError("apify_balance_format_unknown")

    user_data = account_get(ACCOUNT_PLAN)
    plan = user_data.get("plan")
    if not isinstance(plan, dict):
        raise ValueError("apify_included_credit_unknown")
    included = plan.get("monthlyUsageCreditsUsd")
    if type(included) not in (int, float) or not math.isfinite(included) or included <= 0:
        raise ValueError("apify_included_credit_unknown")
    base_price = plan.get("monthlyBasePriceUsd")
    if type(base_price) not in (int, float) or not math.isfinite(base_price) or base_price < 0:
        raise ValueError("apify_included_credit_unknown")
    # A Free plan is worth at most $5. Any other tier must still be within
    # prepaid credits so the test never causes additional pay-as-you-go fees.
    if base_price == 0 and included > FREE_MONTHLY_CREDIT_USD + .01:
        raise ValueError("apify_included_credit_unknown")
    if spent + cap > LOCAL_MONTHLY_RESERVE_USD + 1e-8:
        raise ValueError("apify_free_monthly_reserve_exhausted")
    if spent + cap + ACCOUNT_HEADROOM_CUSHION_USD > min(maximum, included) + 1e-8:
        raise ValueError("apify_remaining_included_credit_low")
    return True


def active_backoff(root, now):
    path = Path(root) / COOLDOWN_FILE
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        raise ValueError("Corrupt Apify cooldown state; refusing provider request") from None
    if not isinstance(obj, dict):
        raise ValueError("Invalid Apify cooldown state")
    try:
        until = datetime.fromisoformat(obj["until"].replace("Z", "+00:00"))
        if until.tzinfo is None:
            raise ValueError("Timezone missing")
    except (ValueError, KeyError, AttributeError, TypeError):
        raise ValueError("Invalid Apify cooldown date") from None
    return now < until


def permitted_nonrestrictive_retry(root, env, now):
    """Allow one explicit corrected-input test, never lift an HTTP restriction.

    A failed input (HTTP 400) previously wrote a generic 2-hour
    provider_unavailable backoff. Only the owner one-shot test may override
    THAT state. Network restrictions and 24-hour restricted backoffs remain
    intact. The marker is encrypted and durable before the new provider POST.
    """
    if not (env.get("AET_ONE_TIME_FACEBOOK_RETRY") == "I_ACCEPT_ONE_CORRECTED_INPUT_TEST"
            and env.get("GITHUB_REPOSITORY") == "ali00978/aet-hunter-public"
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_EVENT_NAME") == "push"
            and env.get("GITHUB_RUN_ATTEMPT") == "1"
            and env.get("GITHUB_WORKFLOW") ==
                    "AET Facebook one-time live integration to AET Pro"):
        return False
    if (Path(root) / ONE_TIME_RETRY_FILE).exists():
        return False
    path = Path(root) / COOLDOWN_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict)
                or data.get("reason") != "provider_unavailable"):
            return False
        moment = datetime.fromisoformat(data["until"].replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return False
        return now < moment
    except (OSError, ValueError, AttributeError, TypeError, KeyError):
        return False


def describe_provider_http_error(error):
    """Small allowlist of Apify machine error types; no raw error bodies."""
    try:
        blob = error.read(8193)
        if len(blob) > 8192:
            return None
        parsed = json.loads(blob.decode("utf-8"))
        item = parsed.get("error") if isinstance(parsed, dict) else None
        code = item.get("type") if isinstance(item, dict) else None
        allowed = {
            "invalid-input", "invalid-input-schema", "invalid-parameter",
            "max-total-charge-usd-below-minimum",
            "actor-run-failed", "actor-is-not-rented",
            "actor-can-not-be-rented", "actor-disabled",
            "apify-plan-required-to-use-paid-actor",
            "not-enough-usage-to-run-paid-actor",
            "limit-reached", "too-many-requests",
        }
        return code if isinstance(code, str) and code in allowed else None
    except (OSError, ValueError, UnicodeError, TypeError):
        return None


def set_backoff(root, now, http_code=None):
    from datetime import timedelta
    hours = 24 if http_code in (401, 402, 403, 429) else 2
    atomic_json(Path(root) / COOLDOWN_FILE,
                {"until": (now + timedelta(hours=hours)).isoformat(),
                 "reason": "http_restricted" if http_code in (401, 402, 403, 429)
                 else "provider_unavailable"})


def reserve(root, env, persist, now=None):
    """Reserve even if Apify later times out; persist before any chargeable call."""
    now = now or datetime.now(timezone.utc)
    monthly, cap = bounded_budget(env)
    path = Path(root) / "apify_budget.json"
    try:
        budget = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        budget = {"month": now.strftime("%Y-%m"), "calls": 0, "reserved_usd": 0.0}
    except (ValueError, OSError):
        raise ValueError("Corrupt or unreadable Apify budget; refusing paid call") from None
    if not isinstance(budget, dict):
        raise ValueError("Invalid Apify budget data")
    month = now.strftime("%Y-%m")
    if budget.get("month") != month:
        budget = {"month": month, "calls": 0, "reserved_usd": 0.0}
    calls = budget.get("calls")
    spent = budget.get("reserved_usd", 0)
    if (type(calls) is not int or not 0 <= calls <= MAX_MONTHLY_STARTS or
        type(spent) not in (int, float) or not math.isfinite(spent) or spent < 0):
        raise ValueError("Invalid persistent Apify budget counters")
    if calls >= MAX_MONTHLY_STARTS or spent + cap > monthly + 0.00001:
        raise ValueError("Apify monthly call/spending cap reached")
    next_budget = {"month": month, "calls": calls + 1,
                   "reserved_usd": round(spent + cap, 2)}
    atomic_json(path, next_budget)
    persist()  # Durable reservation BEFORE provider POST; never retry a timeout.
    return cap


# Rotate 16 frequent resale models across three daily UTC time windows.
# Strict keyword and vehicle-only filters prevent paying for parts or scooters.
# Four searches x four detailed cars is deliberately within ~$0.05/run at
# the Actor's published search/listing/detail event pricing. Actual charges
# remain guarded by Apify's maxTotalChargeUsd limit.
SEARCH_GROUPS = (
    ("corsa", "clio", "aygo", "polo"),
    ("picanto", "206", "punto", "fiesta"),
    ("alto", "i10", "c1", "fabia"),
    ("astra", "twingo", "ibiza", "207"),
)

def query_group(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware Actor scheduling required")
    utc = now.astimezone(timezone.utc)
    # Scheduled UTC hours: 02, 10 and 18. Rotate model groups every slot.
    slot = utc.date().toordinal()*3 + utc.hour//8
    return list(SEARCH_GROUPS[slot % len(SEARCH_GROUPS)])


def provider_vehicle_fields(raw):
    """Use details without storing arbitrary private provider/seller fields."""
    from marktplaats_data import parse_mileage
    if not isinstance(raw,dict):
        return {}
    info = raw.get("vehicle")
    if not isinstance(info,dict):
        return {}
    cleaned = {}
    make = info.get("make")
    model = info.get("model")
    if isinstance(make,str) and isinstance(model,str):
        cleaned["vehicle_identity_hint"] = (make[:35],model[:45])
    year = info.get("year")
    if type(year) is int and 1950 <= year <= 2030:
        cleaned["year"] = year
    mileage = parse_mileage(info.get("mileage"))
    unit = str(info.get("mileageUnit") or "").upper()
    if mileage is not None:
        if unit in ("MILES","MI"):
            mileage = round(mileage * 1.609344)
        elif unit not in ("KM","KILOMETERS","KILOMETRES",""):
            mileage = None
        if mileage is not None:
            cleaned["mileage_km"] = mileage
    seller = str(info.get("sellerType") or "").upper()
    if seller == "PRIVATE_SELLER": cleaned["seller_type"] = "particulier"
    elif seller in ("DEALER","DEALERSHIP","BUSINESS_SELLER"):
        cleaned["seller_type"] = "handelaar"
    return cleaned


def fetch_actor(token, cap, opener=None, now=None):
    """One bounded POST. A timeout may have started a billable Actor."""
    if not isinstance(token, str) or len(token.strip()) < 15:
        raise ValueError("Missing Apify token")
    query = urlencode({"timeout": 90, "memory": 256,
                       "maxItems": MAX_ITEMS_PER_RUN,
                       "maxTotalChargeUsd": str(cap)})
    payload = {
        "searchQueries": query_group(now),
        "location": "Dronten, Netherlands",
        "strictKeywordMatch": True,
        "vehiclesOnly": True,
        "includeDetails": True,
        "excludeDownPaymentAds": True,
        "excludeKeywords": [
            "stuurwiel", "velgen", "onderdelen", "sloopauto", "gezocht",
            "auto onderdelen", "winterbanden", "bumper", "scooter"
        ],
        "daysSinceListed": "30",
        "maxItemsPerSearch": 4,
        "minPrice": 350,
        "maxPrice": 3000,
        "sortNewestFirst": True,
    }
    request = Request(BASE + "?" + query, method="POST",
                      data=json.dumps(payload).encode("utf-8"),
                      headers={"Authorization": "Bearer " + token.strip(),
                               "Content-Type": "application/json",
                               "Accept": "application/json"})
    opener = opener or build_opener(NoRedirect())
    with opener.open(request, timeout=115) as response:
        if response.status != 200:
            raise ValueError("Provider non-success response")
        raw = response.read(1_500_001)
    if len(raw) > 1_500_000:
        raise ValueError("Provider response larger than safety limit")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("Provider returned non-list dataset")
    return data[:MAX_ITEMS_PER_RUN]


def collect(root, env, persist, request_fn=None, now=None, account_check=None):
    """Fail closed without losing good history or resetting charge counters."""
    import facebook_hunter
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    if env.get("AET_FACEBOOK_ENABLED") != "true":
        return {"status": "disabled"}
    token = env.get("APIFY_TOKEN", "")
    if len(token.strip()) < 15:
        raise ValueError("Missing Apify credential")
    monthly, cap = bounded_budget(env)
    status = {"checked_at": now.isoformat(), "status": "preflight_failed",
              "provider": "Apify HydeData"}

    # The one-off exception applies ONLY to a prior 2-hour input/network
    # cooldown. It never bypasses a recorded HTTP account restriction.
    retry_exception = False
    try:
        if active_backoff(root, now):
            if permitted_nonrestrictive_retry(root, env, now):
                retry_exception = True
            else:
                status["status"] = "provider_cooldown"
                atomic_json(root / "apify_status.json", status)
                persist()
                return status
        if account_check is None:
            verify_free_headroom(token, cap)
        else:
            account_check(token, cap)
    except ValueError as error:
        # The only externally visible diagnostic is one of these fixed
        # codes. Never log tokens, raw API responses, or credit amounts.
        reason = str(error)
        allowed_reasons = ("apify_balance_format_unknown",
                           "apify_account_data_unknown",
                           "apify_included_credit_unknown",
                           "apify_free_monthly_reserve_exhausted",
                           "apify_remaining_included_credit_low")
        status["preflight_reason"] = (
            reason if reason in allowed_reasons else "provider_gate_unknown")
        atomic_json(root / "apify_status.json", status)
        persist()
        return status

    try:
        reserve(root, env, persist, now=now)
    except ValueError:
        status["status"] = "budget_cap_reached"
        atomic_json(root / "apify_status.json", status)
        persist()
        return status

    if retry_exception:
        # Must be durable before the potentially billable request begins:
        # no second exception, even on timeout or process termination.
        atomic_json(root / ONE_TIME_RETRY_FILE,
                    {"used_at": now.isoformat(),
                     "reason": "one_corrected_input_test"})
        persist()
    status["status"] = "request_failed"
    try:
        rows = request_fn(token, cap) if request_fn else fetch_actor(token, cap, now=now)
        if not rows:
            # Zero results can signal provider/API breakage; do not erase the
            # previous saved candidates or synchronize an empty Facebook feed.
            status["status"] = "empty_response"
        else:
            history_path = root / "apify_seen.json"
            try:
                history = json.loads(history_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                history = {}
            except (OSError, ValueError):
                raise ValueError("Invalid Facebook history") from None
            if not isinstance(history, dict):
                raise ValueError("Invalid Facebook history structure")
            accepted = {}
            allowed = ("id", "url", "title", "price", "city", "location",
                       "listedAt", "listed_at", "year", "year_detected",
                       "mileage_km", "mileage", "seller_type", "description",
                       "photo_urls", "images", "photos", "pictures",
                       "imageUrls", "photoUrls", "photoUrl", "image", "imageUrl",
                       "image_url", "thumbnailUrl", "thumbnail", "photo",
                       "picture", "primaryImage", "coverPhoto", "mainImage",
                       "listingImage", "currency", "priceFormatted")
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                if raw.get("isSold") is True or raw.get("isPending") is True:
                    continue
                url = raw.get("url")
                if not isinstance(url, str) or len(url) > 600:
                    continue
                first_seen = history.get(url)
                normalized = {key: raw[key] for key in allowed if key in raw}
                vehicle = provider_vehicle_fields(raw)
                normalized.update({key:value for key,value in vehicle.items()
                                   if key != "vehicle_identity_hint"})
                # Never convert currencies silently. Make/source identity
                # remains unverifiable unless Facebook provided it.
                if isinstance(normalized.get("description"), str):
                    normalized["description"] = normalized["description"][:600]
                normalized["first_seen_at"] = first_seen or now.isoformat()
                provider_seller = str(raw.get("sellerType") or "").upper()
                if provider_seller == "PRIVATE_SELLER":
                    normalized["seller_type"] = "particulier"
                elif provider_seller in ("DEALER", "DEALERSHIP", "BUSINESS_SELLER"):
                    normalized["seller_type"] = "handelaar"
                if "vehicle_identity_hint" in vehicle:
                    from facebook_vehicle_filter import term
                    make,model = vehicle["vehicle_identity_hint"]
                    # A genuine conflicting title/structured make requires
                    # manual investigation, not automatic dealer import.
                    if not (term(str(normalized.get("title") or "").casefold(),
                                 make.casefold()) or
                            term(str(normalized.get("title") or "").casefold(),
                                 model.casefold())):
                        continue
                car = facebook_hunter.normalize(normalized, "Apify", now,
                                                 provider_at=now)
                if car is None:
                    continue
                if car["id"] not in accepted:
                    # Only bounded, validated photo URLs and normalized
                    # classification are stored, never unrelated seller data.
                    normalized["photo_urls"] = car["photo_urls"]
                    normalized["seller_type"] = car["seller_classification"]
                    accepted[car["id"]] = normalized
                    if not first_seen:
                        history[url] = now.isoformat()
            status.update({"received": len(rows), "accepted": len(accepted),
                           "new_candidates": sum(
                               r["first_seen_at"] == now.isoformat()
                               for r in accepted.values())})
            if accepted:
                atomic_json(history_path, dict(list(history.items())[-4000:]))
                atomic_json(root / "apify_kandidaten.json", list(accepted.values()))
                status["status"] = "ok"
            else:
                # The provider returned irrelevant parts or malformed entries,
                # not proof that all historical cars have vanished.
                status["status"] = "no_vehicle_candidates"
    except HTTPError as error:
        status.update({"status": "http_error", "http_status": error.code})
        safe_type = describe_provider_http_error(error)
        if safe_type is not None:
            status["provider_error_type"] = safe_type
        set_backoff(root, now, http_code=error.code)
    except (URLError, OSError, ValueError, TimeoutError, TypeError) as error:
        status.update({"status": "request_failed", "error_type": type(error).__name__})
        set_backoff(root, now)
    finally:
        atomic_json(root / "apify_status.json", status)
        persist()
    return status
