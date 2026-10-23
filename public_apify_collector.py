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
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit
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
PENDING_ACTOR_FILE = "apify_pending.json"
ACTOR_INFO = "https://api.apify.com/v2/actors/" + ACTOR
ACTOR_RUNS = "https://api.apify.com/v2/actor-runs"
PENDING_WINDOW_BEFORE = timedelta(minutes=2)
PENDING_WINDOW_AFTER = timedelta(minutes=5)
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


def actor_payload(now=None):
    """Shared, trade-focused actor input for sync compatibility and async starts."""
    return {
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


def fetch_actor(token, cap, opener=None, now=None):
    """One bounded POST. A timeout may have started a billable Actor."""
    if not isinstance(token, str) or len(token.strip()) < 15:
        raise ValueError("Missing Apify token")
    query = urlencode({"timeout": 90, "memory": 256,
                       "maxItems": MAX_ITEMS_PER_RUN,
                       "maxTotalChargeUsd": str(cap)})
    payload = actor_payload(now)
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


class PendingActorError(RuntimeError):
    """An Actor may have started, so never initiate a second paid one."""


class FinishedActorError(RuntimeError):
    """Existing Actor finished unsuccessfully and may have consumed credits."""


def start_actor_async(token, cap, opener=None, now=None):
    """Start ONCE, return run identity immediately, never hold a long POST open.

    Apify documents this asynchronous endpoint as the resilient alternative
    to run-sync-get-dataset-items. maxTotalChargeUsd remains server enforced.
    """
    if not isinstance(token,str) or len(token.strip())<15:
        raise ValueError("Missing Apify token")
    if type(cap) not in (int,float) or not 0 < cap <= DEFAULT_MAX_RUN_USD:
        raise ValueError("Apify Actor charge exceeds configured cap")
    args=urlencode({"timeout":180, "memory":256,"maxItems":MAX_ITEMS_PER_RUN,
                    "maxTotalChargeUsd":str(cap),"restartOnError":"false"})
    url="https://api.apify.com/v2/acts/"+ACTOR+"/runs?"+args
    request=Request(url,method="POST",
        data=json.dumps(actor_payload(now)).encode("utf-8"),
        headers={"Authorization":"Bearer "+token.strip(),
                 "Content-Type":"application/json","Accept":"application/json"})
    opener=opener or build_opener(NoRedirect())
    with opener.open(request,timeout=20) as response:
        if response.status not in (200,201):
            raise ValueError("Unexpected Apify run start HTTP status")
        raw=response.read(60_001)
    if len(raw)>60_000:
        raise ValueError("Oversized Apify run-start confirmation")
    output=json.loads(raw.decode("utf-8"))
    run=output.get("data") if isinstance(output,dict) else None
    run_id=run.get("id") if isinstance(run,dict) else None
    actor_id=run.get("actId") if isinstance(run,dict) else None
    if (not isinstance(run_id,str) or not re.fullmatch(r"[A-Za-z0-9]{8,35}",run_id)
            or not isinstance(actor_id,str)
            or not re.fullmatch(r"[A-Za-z0-9]{8,35}",actor_id)):
        raise ValueError("Apify did not confirm a verifiable run identity")
    options=run.get("options")
    limit=options.get("maxTotalChargeUsd") if isinstance(options,dict) else None
    if type(limit) not in (int,float) or abs(float(limit)-float(cap))>0.00001:
        raise ValueError("Apify did not confirm a safe run charge limit")
    return {"id":run_id,"actId":actor_id}


def await_started_actor(token, marker, getter=None, pause=None, attempts=10):
    """Read-only polling of ONE paid run, with bounded time and GET requests."""
    pause=pause or time.sleep
    for index in range(attempts):
        state, rows=recover_uncertain_run(token,marker,getter=getter)
        if state != "pending":
            return state, rows
        if index < attempts-1:
            pause(12)
    return "pending", None


def launch_and_collect(token, cap, root, persist, now=None,
                       starter=None, wait_for_result=None):
    """Checkpoint known run ID before polling; preserve it on any error."""
    now=now or datetime.now(timezone.utc)
    starter=starter or start_actor_async
    launched=starter(token,cap,now=now)
    # Refuse to overwrite a malformed pending marker: no extra paid calls.
    marker=load_pending_actor(root)
    if marker is None:
        raise ValueError("Missing durable pre-POST run marker")
    marker["run_id"]=launched["id"]
    marker["actor_id"]=launched["actId"]
    atomic_json(Path(root)/PENDING_ACTOR_FILE,marker)
    persist()
    waiter=wait_for_result or await_started_actor
    try:
        result, rows=waiter(token,marker)
    except HTTPError as error:
        # The paid POST has ALREADY succeeded. A 401/429 on a subsequent
        # free status GET is NOT evidence that the Actor failed to start.
        # Keep its encrypted run ID and respect account cooldown.
        set_backoff(root,now,http_code=error.code)
        raise PendingActorError("Apify status GET interrupted") from None
    except (URLError, TimeoutError, OSError, ValueError, TypeError,
            KeyError, UnicodeError):
        # Polling can fail while the Actor continues to run. Never drop
        # the paid-start marker or send another billable POST.
        set_backoff(root,now)
        raise PendingActorError("Apify status could not be verified") from None
    if result=="recovered" and isinstance(rows,list):
        return rows
    if result=="failed":
        raise FinishedActorError("Apify Actor did not complete successfully")
    raise PendingActorError("Apify Actor result not yet verified")


def read_only_provider_get(url, token, opener=None):
    """Bounded Apify GET; never visit an arbitrary URL with the API token."""
    from urllib.parse import parse_qs
    import re
    parsed = urlsplit(url)
    safe_host = (parsed.scheme == "https" and parsed.netloc == "api.apify.com"
                 and not (parsed.username or parsed.password or parsed.fragment))
    if not safe_host:
        raise ValueError("Unapproved Apify recovery destination")
    path = parsed.path
    if url == ACTOR_INFO:
        pass
    elif path == "/v2/actor-runs":
        opts = parse_qs(parsed.query)
        if (set(opts) != {"limit","desc","startedAfter","startedBefore"}
                or opts["limit"] != ["50"] or opts["desc"] != ["1"]):
            raise ValueError("Unapproved Apify run-list query")
        for key in ("startedAfter","startedBefore"):
            if len(opts[key]) != 1 or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.000Z",
                    opts[key][0]):
                raise ValueError("Unapproved Apify run search timestamp")
    elif re.fullmatch(r"/v2/actor-runs/[A-Za-z0-9]{8,35}",path):
        if parsed.query:
            raise ValueError("Unapproved Apify run details query")
    elif re.fullmatch(r"/v2/datasets/[A-Za-z0-9]{8,35}/items",path):
        if parsed.query != "format=json&limit=80&clean=1":
            raise ValueError("Unapproved Apify dataset query")
    else:
        raise ValueError("Unapproved Apify recovery endpoint")
    request = Request(url, method="GET", headers={
        "Authorization": "Bearer " + token.strip(),
        "Accept": "application/json",
        "User-Agent": "AET-Hunter-ReadOnly-Provider-Recovery/1",
    })
    opener = opener or build_opener(NoRedirect())
    with opener.open(request, timeout=15) as reply:
        if reply.status != 200:
            raise ValueError("Apify recovery GET unsuccessful")
        raw = reply.read(1_500_001)
    if len(raw) > 1_500_000:
        raise ValueError("Apify recovery response too large")
    return json.loads(raw.decode("utf-8"))


def recover_uncertain_run(token, marker, getter=None):
    """No new actor starts. Identify ONE old billed run or fail closed.

    A timeout on Apify's synchronous endpoint does NOT mean the Actor failed.
    The next scan attempts bounded GET-only reconciliation before considering
    a second payable POST. Multiple matching runs are deliberately ambiguous.
    """
    import re
    getter = getter or read_only_provider_get
    if not isinstance(marker,dict) or marker.get("actor") != ACTOR:
        raise ValueError("Invalid pending Apify Actor marker")
    try:
        started = datetime.fromisoformat(marker["started_at"].replace("Z","+00:00"))
    except (KeyError, ValueError, AttributeError, TypeError):
        raise ValueError("Invalid pending Apify Actor time") from None
    if started.tzinfo is None:
        raise ValueError("Pending actor timestamp lacks timezone")
    now = datetime.now(timezone.utc)
    if started > now + timedelta(minutes=15):
        raise ValueError("Pending actor timestamp is in the future")
    started = started.astimezone(timezone.utc)
    fmt = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    known_run=marker.get("run_id")
    if known_run is not None:
        # The asynchronous POST already returned both IDs. The GET below
        # verifies the run's actId, so fetching actor metadata on EVERY poll
        # adds requests without increasing confidence.
        actor_id=marker.get("actor_id")
        if (not isinstance(actor_id,str) or
                not re.fullmatch(r"[A-Za-z0-9]{8,35}",actor_id)):
            raise ValueError("Unverifiable persisted Apify Actor identity")
        if (not isinstance(known_run,str) or
                not re.fullmatch(r"[A-Za-z0-9]{8,35}",known_run)):
            raise ValueError("Invalid persisted Apify run ID")
        id_=known_run
    else:
        # Legacy synchronous timeouts have no confirmed run ID: verify
        # the actor metadata and find exactly one run in the time window.
        actor = getter(ACTOR_INFO, token)
        details = actor.get("data") if isinstance(actor,dict) else None
        actor_id = details.get("id") if isinstance(details,dict) else None
        if (not isinstance(actor_id,str) or
                not re.fullmatch(r"[A-Za-z0-9]{8,35}",actor_id)):
            raise ValueError("Apify Actor identity could not be verified")
        expected_id=marker.get("actor_id")
        if expected_id is not None and expected_id!=actor_id:
            raise ValueError("Persisted run has mismatched Apify Actor identity")
        # Legacy timed-out synchronous request: identify exactly one run
        # within its bounded original time window, never guess by recency.
        query = urlencode({"limit":50,"desc":1,
                           "startedAfter":fmt(started-PENDING_WINDOW_BEFORE),
                           "startedBefore":fmt(started+PENDING_WINDOW_AFTER)})
        runs = getter(ACTOR_RUNS+"?"+query, token)
        payload = runs.get("data") if isinstance(runs,dict) else None
        rows = payload.get("items") if isinstance(payload,dict) else None
        if not isinstance(rows,list) or len(rows)>=50:
            raise ValueError("Unverifiable Apify run search")
        matching = []
        for row in rows:
            if not isinstance(row,dict) or row.get("actId") != actor_id:
                continue
            try:
                dt = datetime.fromisoformat(row["startedAt"].replace("Z","+00:00"))
            except (KeyError, ValueError, AttributeError, TypeError):
                continue
            if dt.tzinfo and started-PENDING_WINDOW_BEFORE <= dt <= started+PENDING_WINDOW_AFTER:
                matching.append(row)
        if len(matching) != 1:
            return "unresolved", None
        id_ = matching[0].get("id")
        if not isinstance(id_,str) or not re.fullmatch(r"[A-Za-z0-9]{8,35}",id_):
            raise ValueError("Invalid Apify run reference")
    full = getter(ACTOR_RUNS+"/"+id_,token)
    run = full.get("data") if isinstance(full,dict) else None
    if not isinstance(run,dict) or run.get("id") != id_ or run.get("actId") != actor_id:
        raise ValueError("Mismatched Apify run details")
    status = run.get("status")
    if status == "SUCCEEDED":
        data_id = run.get("defaultDatasetId")
        if not isinstance(data_id,str) or not re.fullmatch(r"[A-Za-z0-9]{8,35}",data_id):
            raise ValueError("Apify completed run lacks dataset")
        restored = getter("https://api.apify.com/v2/datasets/"+data_id+
                          "/items?format=json&limit=80&clean=1",token)
        if not isinstance(restored,list) or len(restored)>MAX_ITEMS_PER_RUN:
            raise ValueError("Apify recovered dataset format invalid")
        return "recovered", restored
    if status in ("READY","RUNNING","TIMING-OUT","ABORTING"):
        return "pending", None
    if status in ("FAILED","ABORTED","TIMED-OUT"):
        return "failed", None
    return "unresolved", None


def load_pending_actor(root):
    path = Path(root) / PENDING_ACTOR_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        raise ValueError("Unreadable pending Apify Actor state") from None
    if not isinstance(data,dict) or data.get("actor") != ACTOR:
        raise ValueError("Corrupt pending Apify Actor state")
    time_value = data.get("started_at")
    if not isinstance(time_value,str) or len(time_value)>60:
        raise ValueError("Invalid pending Apify timestamp")
    try:
        moment = datetime.fromisoformat(time_value.replace("Z","+00:00"))
    except ValueError:
        raise ValueError("Invalid pending Apify timestamp") from None
    if moment.tzinfo is None:
        raise ValueError("Pending Apify timestamp lacks timezone")
    return data


def collect(root, env, persist, request_fn=None, now=None, account_check=None, recovery_fn=None):
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

    recovered_rows = None
    try:
        pending = load_pending_actor(root)
    except ValueError:
        status["status"] = "provider_pending"
        status["preflight_reason"] = "pending_state_corrupt"
        atomic_json(root / "apify_status.json",status)
        persist()
        return status
    if pending is not None:
        # Respect explicit 401/402/403/429 account restrictions even when
        # recovering an already-paid Actor. Regular network backoff does
        # not block read-only recovery of an uncertain result.
        if active_backoff(root,now):
            backoff=json.loads((root / COOLDOWN_FILE).read_text(encoding="utf-8"))
            if isinstance(backoff,dict) and backoff.get("reason")=="http_restricted":
                status["status"]="provider_cooldown"
                atomic_json(root / "apify_status.json",status)
                persist()
                return status
        # This is GET-only and runs before quota/backoff checks. It cannot
        # launch or charge a second Actor while the prior POST is uncertain.
        try:
            recovery_state, recovered_rows = (
                recovery_fn(token,pending) if recovery_fn
                else recover_uncertain_run(token,pending))
        except HTTPError as error:
            if error.code in (401,402,403,429):
                set_backoff(root,now,http_code=error.code)
            recovery_state, recovered_rows = "unresolved", None
        except (ValueError, URLError, TimeoutError, OSError,
                TypeError, KeyError, UnicodeError):
            recovery_state, recovered_rows = "unresolved", None
        if recovery_state == "failed":
            (root / PENDING_ACTOR_FILE).unlink(missing_ok=True)
            set_backoff(root,now)
            status["status"] = "provider_run_failed"
            atomic_json(root / "apify_status.json",status)
            persist()
            return status
        if recovery_state != "recovered" or not isinstance(recovered_rows,list):
            status["status"] = "provider_pending"
            status["preflight_reason"] = "awaiting_existing_actor"
            atomic_json(root / "apify_status.json",status)
            persist()
            return status
        status["recovered_existing"] = True
        # The existing actor has already been billed and reserved. Reuse
        # its returned records without a new reserve(), POST or spend.

    if recovered_rows is None:
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
    if recovered_rows is None:
        # Save the uncertain Actor start BEFORE the potentially billable POST.
        # Even a process crash or HTTP timeout must not trigger a duplicate
        # paid call on the next scheduled scan.
        atomic_json(root / PENDING_ACTOR_FILE,
                    {"actor":ACTOR, "started_at":now.isoformat()})
        persist()
    try:
        rows = (recovered_rows if recovered_rows is not None else
                (request_fn(token, cap) if request_fn
                 else launch_and_collect(token,cap,root,persist,now=now)))
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
    except PendingActorError:
        status.update({"status":"provider_pending",
                       "preflight_reason":"awaiting_existing_actor"})
    except FinishedActorError:
        status["status"]="provider_run_failed"
        set_backoff(root,now)
        (root / PENDING_ACTOR_FILE).unlink(missing_ok=True)
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
        completed = status.get("status") in (
            "ok", "empty_response", "no_vehicle_candidates")
        definite_http_rejection = (status.get("status") == "http_error" and
            status.get("http_status") in (400, 401, 402, 403, 404, 422, 429))
        if completed or definite_http_rejection:
            (root / PENDING_ACTOR_FILE).unlink(missing_ok=True)
        atomic_json(root / "apify_status.json", status)
        persist()
    return status
