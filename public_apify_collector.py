"""Capped independent Apify collector; data lives in a private Modal Volume.

Do not call this on public GitHub Actions. No token, raw listing or provider
response is written to GitHub or public logs.
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
MAX_MONTHLY_STARTS = 100


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
    """Explicit opt-in monetary budget. No implicit pay-as-you-go spending."""
    def money(key, default):
        try:
            raw = float(env.get(key, str(default)))
        except (ValueError, TypeError, OverflowError):
            raise ValueError("Invalid Apify budget configuration") from None
        if not math.isfinite(raw):
            raise ValueError("Non-finite Apify budget")
        return raw
    monthly = money("AET_APIFY_MONTHLY_BUDGET_USD", 0)
    per_run = money("AET_APIFY_MAX_CHARGE_USD", 0.10)
    if not 0 < monthly <= 100 or not 0 < per_run <= 0.50:
        raise ValueError("Apify budget needs positive approved limits")
    return round(monthly, 2), round(per_run, 2)


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


def fetch_actor(token, cap, opener=None):
    """One bounded POST. A timeout may have started a billable Actor."""
    if not isinstance(token, str) or len(token.strip()) < 15:
        raise ValueError("Missing Apify token")
    query = urlencode({"timeout": 90, "memory": 256, "maxItems": 30,
                       "maxTotalChargeUsd": str(cap)})
    payload = {"searchQueries": [], "location": "Dronten, Netherlands",
               "categories": ["vehicles"], "maxItemsPerSearch": 30,
               "vehiclesOnly": True, "addListingDetails": False}
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
    return data[:150]


def collect(root, env, persist, request_fn=None, now=None):
    """Return aggregate status; retain only filtered vehicle data privately."""
    import facebook_hunter
    now = now or datetime.now(timezone.utc)
    root = Path(root)
    if env.get("AET_FACEBOOK_ENABLED") != "true":
        return {"status": "disabled"}
    token = env.get("APIFY_TOKEN", "")
    if len(token.strip()) < 15:
        raise ValueError("Missing Apify credential")
    # Check spending configuration before allocating a chargeable reservation.
    bounded_budget(env)
    cap = reserve(root, env, persist, now=now)
    status = {"checked_at": now.isoformat(), "status": "request_failed",
              "provider": "Apify HydeData"}
    try:
        rows = request_fn(token, cap) if request_fn else fetch_actor(token, cap)
        history_path = root / "apify_seen.json"
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            history = {}
        except (OSError, ValueError):
            raise ValueError("Invalid Facebook history; preserving provider call budget") from None
        if not isinstance(history, dict):
            raise ValueError("Invalid Facebook history structure")
        accepted = {}
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            url = raw.get("url")
            first_seen = history.get(url) if isinstance(url, str) else None
            normalized = dict(raw)
            normalized["first_seen_at"] = first_seen or now.isoformat()
            car = facebook_hunter.normalize(normalized, "Apify", now,
                                             provider_at=now)
            if car is None:
                continue
            if car["id"] not in accepted:
                normalized["photo_urls"] = car["photo_urls"]
                normalized["seller_type"] = car["seller_classification"]
                accepted[car["id"]] = normalized
                if not first_seen:
                    history[url] = now.isoformat()
        atomic_json(history_path, dict(list(history.items())[-4000:]))
        atomic_json(root / "apify_kandidaten.json", list(accepted.values()))
        status.update({"status": "ok", "received": len(rows),
                       "accepted": len(accepted), "new_candidates": sum(
                           r["first_seen_at"] == now.isoformat()
                           for r in accepted.values())})
    except (HTTPError, URLError, OSError, ValueError, TimeoutError, TypeError) as error:
        status["status"] = "http_error" if isinstance(error, HTTPError) else "request_failed"
        status["error_type"] = type(error).__name__
        if isinstance(error, HTTPError):
            status["http_status"] = error.code
    finally:
        atomic_json(root / "apify_status.json", status)
        persist()
    return status
