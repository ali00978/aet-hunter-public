"""Private, acknowledged-only deduplication of Hunter-to-PRO deliveries.

Every healthy scan may observe old ads again, but unchanged cars must not be
sent as new discoveries. Only after AET Pro confirms receipt do we advance
the small per-platform checkpoint. No extra marketplace or provider calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FILE = "hunter_delivery_checkpoint.json"
SCHEMA = "aet.hunter.delivery-delta.v1"
PLATFORMS = ("marktplaats", "facebook")
# Fields are genuine sale/research changes, NOT the scanner clock, rank,
# signed image URLs, freshness label or the rolling comparison sample.
BUSINESS_FIELDS = (
    "id", "source", "title", "year", "mileage_km",
    "seller_classification", "location_label", "price_type",
    "asking_price_eur", "minimum_bid_eur", "search_card_hint_eur",
)


def fingerprint(candidate):
    if not isinstance(candidate, dict):
        raise ValueError("Invalid Hunter candidate")
    if not isinstance(candidate.get("id"), str) or not isinstance(candidate.get("source"), str):
        raise ValueError("Missing stable Hunter identity")
    value = {key: candidate.get(key) for key in BUSINESS_FIELDS}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load(root):
    path = Path(root) / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": SCHEMA, "marktplaats": {}, "facebook": {}}
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA or
            any(not isinstance(data.get(platform), dict) for platform in PLATFORMS)):
        raise ValueError("Invalid encrypted Hunter delivery checkpoint")
    for platform in PLATFORMS:
        for key, val in data[platform].items():
            if (not isinstance(key, str) or len(key) > 55 or
                    not isinstance(val, str) or len(val) != 64 or
                    any(ch not in "0123456789abcdef" for ch in val)):
                raise ValueError("Invalid Hunter delivery fingerprint")
    return data


def plan(combined, platform, recorded):
    if platform not in PLATFORMS:
        raise ValueError("Unknown Hunter platform")
    if (not isinstance(combined, dict) or combined.get("schema") != "aet-pro.hunter.v2"
            or not isinstance(combined.get("candidates"), list)):
        raise ValueError("Invalid combined Hunter export")
    previous = recorded[platform]
    items = [x for x in combined["candidates"] if x.get("source") == platform]
    if len(items) > 30 or len({x.get("id") for x in items}) != len(items):
        raise ValueError("Too many or duplicated Hunter advertisements")
    changed, signatures, first, revised = [], {}, 0, 0
    for item in items:
        key = item["id"]
        sig = fingerprint(item)
        signatures[key] = sig
        if previous.get(key) == sig:
            continue
        changed.append(item)
        if key in previous:
            revised += 1
        else:
            first += 1
    # The ordinary full research snapshot remains saved for traceability.
    # Only this transient sync batch is filtered, with valid v2 counts.
    scoped = dict(combined)
    scoped["candidates"] = changed
    scoped["count"] = len(changed)
    scoped["platform_counts"] = {
        "marktplaats": len(changed) if platform == "marktplaats" else 0,
        "facebook": len(changed) if platform == "facebook" else 0,
    }
    scoped["revision"] = hashlib.sha256(json.dumps(
        sorted((item["id"], item["content_hash"]) for item in changed),
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return scoped, signatures, {"observed": len(items), "new_to_delivery": first,
                                "changed": revised, "repeated": len(items)-len(changed)}


def acknowledge(root, platform, signatures, persist=lambda: None):
    """Call only AFTER remote receipt == true; failure must preserve prior state."""
    if platform not in PLATFORMS or not isinstance(signatures, dict):
        raise ValueError("Invalid Hunter checkpoint update")
    state = load(root)
    merged = dict(state[platform])
    merged.update(signatures)
    # Old ads remain remembered to prevent resurfacing without meaningful
    # changes. Bound encrypted-cache growth independently for each platform.
    state[platform] = dict(list(merged.items())[-3000:])
    path = Path(root) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=".hunter-delta-",
                suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    persist()
