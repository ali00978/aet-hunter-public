"""Private, acknowledged-only deduplication of Hunter-to-PRO deliveries.

Every healthy scan may observe old ads again, but unchanged cars must not be
sent as new discoveries. Only after AET Pro confirms receipt do we advance
the small per-platform checkpoint. No extra marketplace or provider calls.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

FILE = "hunter_delivery_checkpoint.json"
SCHEMA = "aet.hunter.delivery-delta.v1"
PLATFORMS = ("marktplaats", "facebook")
# Fields are genuine sale/research changes, NOT the scanner clock, rank,
# signed image URLs, freshness label or the rolling comparison sample.
# Material scanner notes are stable safety findings. Exclude routine uncertainty
# ("not rechecked"), rank and timestamps to prevent repeated pseudo-updates.
REPAIR_NOTE_PREFIXES = (
    "Airco werkt mogelijk niet:",
    "Cosmetisch/elektrisch herstelpunt:",
    "APK loopt af:",
    "Door scanner gemeld herstelpunt:",
)
# Market valuations are moving research, not a change by the seller. Even
# EUR 100 comparison-price bands oscillate with different reference samples.
# Use an acknowledged-only one-time migration when upgrading existing hashes.
FINGERPRINT_REVISION = 2
PENDING_REBASE = "0" * 64  # impossible normal SHA-256; private state sentinel

BUSINESS_FIELDS = (
    "id", "source", "title", "year", "mileage_km",
    "seller_classification", "location_label", "price_type",
    "asking_price_eur", "minimum_bid_eur", "search_card_hint_eur",
)


def _stable_text(value):
    """Canonicalize presentation-only edits, keeping vehicle model digits."""
    if not isinstance(value, str):
        return value
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"!+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def fingerprint(candidate, legacy=False):
    if not isinstance(candidate, dict):
        raise ValueError("Invalid Hunter candidate")
    if not isinstance(candidate.get("id"), str) or not isinstance(candidate.get("source"), str):
        raise ValueError("Missing stable Hunter identity")
    value = {key: candidate.get(key) for key in BUSINESS_FIELDS}
    if candidate["source"] == "marktplaats" and not legacy:
        # Case, line breaks, punctuation styling and spacing in title/city
        # are not new car offers. Keep older hashes readable for migration.
        for key in ("title", "location_label"):
            value[key] = _stable_text(value[key])
    if candidate["source"] == "marktplaats":
        # A real newly discovered repair risk MUST reach the PRO inbox, even
        # when title, price and mileage have not changed. Generic scan-time
        # warnings are not material and must never generate false updates.
        notes = candidate.get("issues")
        if isinstance(notes, list):
            repair_notes = sorted({note.strip() for note in notes
                                   if isinstance(note, str) and
                                   note.strip().startswith(REPAIR_NOTE_PREFIXES)})
            if repair_notes:
                value["material_repair_notes"] = repair_notes
        # Deliberately EXCLUDE computed market values, comparison sample size,
        # image signed URLs and scanner time. A new comparison sample can move
        # the median significantly even if the seller's offer is unchanged.
        # Only the seller's own price/specs and material technical warnings
        # should count as an update.
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load(root):
    path = Path(root) / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": SCHEMA, "marktplaats": {}, "facebook": {},
                "marktplaats_fingerprint_revision": FINGERPRINT_REVISION}
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA or
            any(not isinstance(data.get(platform), dict) for platform in PLATFORMS)):
        raise ValueError("Invalid encrypted Hunter delivery checkpoint")
    revision = data.get("marktplaats_fingerprint_revision", 1)
    if type(revision) is not int or revision not in (1, FINGERPRINT_REVISION):
        raise ValueError("Unsupported Hunter fingerprint checkpoint revision")
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
    if len(items) > (100 if platform == "marktplaats" else 30) or len({x.get("id") for x in items}) != len(items):
        raise ValueError("Too many or duplicated Hunter advertisements")
    # Existing PRIVATE encrypted checkpoints predate revision 2 and contain
    # the old market-sensitive digest, not the old seller price. We cannot
    # reconstruct that old price from a SHA256 digest. The one-time transition
    # silently re-baselines already acknowledged IDs (but STILL delivers new
    # IDs), rather than flooding AET Pro with every old car as "updated".
    # An actual price change during that single transition will be observable
    # on the next scan. Never migrate merely because this function ran:
    # acknowledge() commits the new revision only after a Worker receipt.
    upgrading = (platform == "marktplaats" and
                 recorded.get("marktplaats_fingerprint_revision", 1) != FINGERPRINT_REVISION)
    changed, signatures, first, revised = [], {}, 0, 0
    for item in items:
        key = item["id"]
        sig = fingerprint(item)
        signatures[key] = sig
        previous_sig = previous.get(key)
        if previous_sig == sig or (
                platform == "marktplaats" and previous_sig is not None
                and (upgrading or previous_sig == PENDING_REBASE or
                     previous_sig == fingerprint(item, legacy=True))):
            # Silently upgrade an unchanged older checkpoint to the new
            # normalized signature AFTER the Worker acknowledges the scan.
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
    if (platform == "marktplaats" and
            state.get("marktplaats_fingerprint_revision", 1) != FINGERPRINT_REVISION):
        # All earlier acknowledged IDs must be rebased lazily, including
        # vehicles currently outside the top 100. Store a validated sentinel
        # for unseen IDs, otherwise they would appear as a false "change" the
        # first time they resurface weeks later.
        merged = {key: PENDING_REBASE for key in merged}
    # Acknowledged candidates that remain active must be the most recent
    # entries in our bounded memory. dict.update() keeps an existing key in
    # its old position, so routinely rescanned old cars would otherwise fall
    # out of the 3,000-ID window and falsely look "new" months later.
    for key, signature in signatures.items():
        merged.pop(key, None)
        merged[key] = signature
    if platform == "marktplaats":
        # The marker, pending IDs and updated signatures share ONE atomic
        # private checkpoint; only a verified AET Pro receipt advances it.
        state["marktplaats_fingerprint_revision"] = FINGERPRINT_REVISION
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
