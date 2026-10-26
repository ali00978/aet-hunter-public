"""Stand-alone Hunter production pipeline.

It imports ONLY source in this PUBLIC repository. Data and snapshots remain in
an external private volume; GitHub Actions is only used to deploy the software.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_HOST = "aet-cars-pro.eltakech100.workers.dev"
EXPECTED_PATH = "/api/integrations/hunter/sync"
STATE_ENV = "AET_HUNTER_STATE_DIR"


def require_sync(env):
    endpoint = env.get("AET_PRO_SYNC_URL", "")
    token = env.get("AET_HUNTER_SYNC_TOKEN", "")
    try:
        p = urlsplit(endpoint)
        valid = (p.scheme == "https" and p.hostname == EXPECTED_HOST and
                 p.path == EXPECTED_PATH and p.port in (None, 443) and
                 not (p.username or p.password or p.query or p.fragment))
    except ValueError:
        valid = False
    if not valid or len(token) < 24:
        raise ValueError("Missing or invalid AET Pro sync configuration")
    return endpoint, token


def receiver_marktplaats_limit(endpoint, opener=None):
    """Negotiate capacity with *our own* AET Pro Worker, never the marketplaces.

    Earlier deployed Workers reject >30. Fail closed to 30 if the harmless
    public health response is missing, offline, redirected or malformed.
    """
    from urllib.request import Request, build_opener, HTTPRedirectHandler

    class NoRedirects(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    if not isinstance(endpoint, str) or not endpoint.endswith(EXPECTED_PATH):
        return 30
    health = "https://" + EXPECTED_HOST + "/api/health"
    opener = opener or build_opener(NoRedirects())
    try:
        request = Request(health, headers={"Accept": "application/json"},
                          method="GET")
        with opener.open(request, timeout=7) as response:
            raw = response.read(4097)
            if response.status != 200 or len(raw) > 4096:
                return 30
        data = json.loads(raw.decode("utf-8"))
        if (isinstance(data, dict) and data.get("status") == "ok"
                and data.get("database") == "connected"
                and type(data.get("hunter_max_marktplaats_candidates")) is int
                and data["hunter_max_marktplaats_candidates"] >= 100):
            return 100
    except (OSError, ValueError, TypeError, UnicodeError, RuntimeError):
        return 30
    return 30


def load_object(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Unexpected status format")
    return payload


def run_scanner_with_progress(scan, interval_seconds=120, emit=None):
    """Keep a GitHub Actions log responsive without revealing any listings.

    Marketplace logs are intentionally redirected to /dev/null so URLs and
    seller data never leave the runner. Progress uses an independent, fixed
    aggregate message and does not make requests, shorten pacing or change
    the scanner's selection/retry logic.
    """
    if emit is None:
        def emit(message):
            os.write(1, (message + "\n").encode("utf-8"))
    if not isinstance(interval_seconds, (int, float)) or interval_seconds <= 0:
        raise ValueError("Invalid heartbeat interval")
    stop = threading.Event()
    started = time.monotonic()
    def heartbeat():
        while not stop.wait(interval_seconds):
            minutes = max(0, int((time.monotonic()-started)//60))
            emit(f"marktplaats: scan is actief na {minutes} minuten; "
                 "geen versnelling of extra verzoeken")
    worker = threading.Thread(target=heartbeat, name="aet-scan-progress",
                              daemon=True)
    emit("marktplaats: scan gestart; resultaten worden privé verwerkt")
    worker.start()
    try:
        return scan()
    finally:
        stop.set()
        worker.join(timeout=1)


def safe_scan_counters(status):
    """Only fixed numeric telemetry; never publish candidate/seller fields."""
    if not isinstance(status,dict):
        return None
    fields=("search_pages_requested","detail_pages_attempted",
            "requests_attempted_total")
    values=[]
    for field in fields:
        value=status.get(field)
        if type(value) is not int or not 0<=value<=145:
            return None
        values.append(value)
    return tuple(values)



def safe_marktplaats_discovery_counts(status):
    """Public Actions logs contain bounded COUNTS, never private listings."""
    if not isinstance(status, dict):
        return None
    fields = ("new_detail_links_planned", "recheck_detail_links_planned",
              "new_delivery_detail_links_planned",
              "already_delivered_detail_links_planned")
    values = []
    for name in fields:
        value = status.get(name)
        if type(value) is not int or not 0 <= value <= 145:
            return None
        values.append(value)
    if (values[0] + values[1] !=
            values[2] + values[3]):
        return None
    return tuple(values)


def checkpoint_history_count(checkpoint, platform):
    """Only return a validated count, never an encrypted-state car identifier."""
    if platform not in ("marktplaats", "facebook") or not isinstance(checkpoint, dict):
        raise ValueError("Invalid checkpoint platform")
    remembered = checkpoint.get(platform)
    if not isinstance(remembered, dict) or len(remembered) > 3000:
        raise ValueError("Invalid delivery checkpoint telemetry")
    return len(remembered)


def durable_scan_identity(env):
    """Use GitHub's stable job identity instead of a fresh nanosecond ID.

    A repeated GitHub attempt belongs to the SAME logical scan, so keep
    its ID stable across retries for AET Pro idempotency. Standalone
    non-GitHub tests use the timestamp fallback.
    """
    run_id = str(env.get("GITHUB_RUN_ID") or "")
    attempt = str(env.get("GITHUB_RUN_ATTEMPT") or "1")
    if (run_id.isascii() and run_id.isdigit()
            and 1 <= len(run_id) <= 20
            and attempt.isascii() and attempt.isdigit()
            and 1 <= len(attempt) <= 5):
        return run_id, "1"
    return str(time.time_ns()), "1"



def stable_marktplaats_batch_run_id(run_id, candidates):
    """Bind an idempotent GitHub scan identity to the exact candidate content.

    If batch 1 was acknowledged but batch 2 failed, a same-run retry may now
    have batch 2 as its first (and only) outbound request. Never reuse the
    original batch-1 scan ID for that different payload. Identical retries
    keep the same ID even when a batch moves to a different index.
    Only digits are used to preserve the current sync envelope contract.
    """
    if not isinstance(run_id, str) or not run_id.isascii() or not run_id.isdigit():
        raise ValueError("Invalid Hunter run identity")
    if not isinstance(candidates, list):
        raise ValueError("Invalid Hunter batch")
    items = [(item.get("id"), item.get("content_hash"))
             for item in candidates if isinstance(item, dict)]
    ids = [key for key, _ in items]
    if (len(items) != len(candidates) or any(
            not isinstance(key, str) or
            not key.startswith("marktplaats:") or
            (digest is not None and (
                not isinstance(digest, str) or len(digest) != 64 or
                any(ch not in "0123456789abcdef" for ch in digest)))
            for key, digest in items)
            or len(set(ids)) != len(ids)):
        raise ValueError("Invalid or duplicated Marktplaats batch identity")
    import hashlib
    # Same IDs but different seller content must NOT reuse an earlier scan ID.
    # Existing Worker idempotency requires identical scan IDs to mean the
    # exact same payload. Missing digests are supported for older test data.
    digest = hashlib.sha256(json.dumps(
        sorted((key, digest or "") for key, digest in items),
        separators=(",", ":")).encode("utf-8")).digest()
    # 64-bit domain-specific numerical fingerprint, preserving leading zeroes.
    suffix = str(int.from_bytes(digest[:8], "big")).zfill(20)
    return run_id + suffix


def provider_status_label(status):
    """Safe aggregate information only, never attacker-supplied strings."""
    if not isinstance(status,dict):
        return "invalid_status"
    allowed = {
        "ok", "empty_response", "no_vehicle_candidates", "request_failed",
        "preflight_failed", "budget_cap_reached", "provider_cooldown",
        "http_error", "provider_pending", "provider_run_failed", "disabled"
    }
    value = status.get("status")
    return value if isinstance(value,str) and value in allowed else "unknown"


def run_platform(platform, persist=lambda: None, reload=lambda: None, env=None):
    env = env if env is not None else os.environ
    if platform not in ("marktplaats", "facebook"):
        raise ValueError("Unsupported Hunter platform")
    if env.get("AET_MODAL_LIVE_ENABLED") != "I_ACCEPT_LIVE_SCANS":
        print("Production Hunter intentionally disabled; no scan")
        return "disabled"
    if platform == "facebook" and env.get("AET_FACEBOOK_ENABLED") != "true":
        print("Facebook disabled by explicit spend control; no paid call")
        return "disabled"
    endpoint, token = require_sync(env)  # Do not spend requests on a broken target.
    root = Path(env.get(STATE_ENV, "/mnt/aet-hunter-state"))
    if not root.is_absolute():
        raise ValueError("Hunter state directory must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    reload()
    import hunter_feed
    import hunter_pro_combined
    import hunter_sync
    import facebook_hunter
    # Restore module paths even on a failed run. This avoids a later scan
    # or offline test accidentally reading an earlier temporary directory.
    previous_paths = (hunter_feed.ROOT, hunter_pro_combined.OUTPUT,
                      hunter_sync.ROOT, facebook_hunter.ROOT)
    # Inject persistent storage without writing any records to the code tree.
    hunter_feed.ROOT = root
    hunter_pro_combined.OUTPUT = root / "hunter_pro_combined.json"
    hunter_sync.ROOT = root
    facebook_hunter.ROOT = root
    cwd = Path.cwd()
    source = "marktplaats" if platform == "marktplaats" else "facebook-apify"
    try:
        os.chdir(root)
        if platform == "marktplaats":
            import scanner
            try:
                with open(os.devnull, "w", encoding="utf-8") as sink:
                    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                        run_scanner_with_progress(scanner.main)
            finally:
                # Keep restriction cooldown and history even if scanner fails.
                persist()
            status = load_object(root / "marktplaats_status.json")
            totals = safe_scan_counters(status)
            if totals:
                print("marktplaats: pagina's=" + str(totals[0]) +
                      ", advertenties=" + str(totals[1]) +
                      ", verzoeken=" + str(totals[2]), flush=True)
            diagnostics = safe_marktplaats_discovery_counts(status)
            if diagnostics:
                print("marktplaats: nooit_eerder_geopend=" + str(diagnostics[0]) +
                      ", hercontrole=" + str(diagnostics[1]) +
                      ", nog_niet_doorgestuurd=" + str(diagnostics[2]) +
                      ", al_eerder_doorgestuurd=" + str(diagnostics[3]),
                      flush=True)
            healthy = status.get("scan_health") == "ok"
        else:
            from public_apify_collector import collect
            status = collect(root, env, persist)
            healthy = status.get("status") == "ok"
        if platform == "facebook":
            print("facebook: provider_status=" + provider_status_label(status),
                  flush=True)
            # Only a fixed Python exception type, never provider URLs,
            # response bodies, listing data or token values.
            kind=status.get("error_type")
            if kind in ("TimeoutError","URLError","HTTPError","ValueError",
                        "TypeError","JSONDecodeError","OSError"):
                print("facebook: provider_exception="+kind,flush=True)
            if status.get("recovered_existing") is True:
                print("facebook: eerdere Apify-run hersteld zonder nieuwe start",
                      flush=True)
        print(platform + ": healthy=" + str(healthy))
        if not healthy:
            return "unhealthy"
        now = datetime.now(timezone.utc)
        # Load only acknowledged deliveries, BEFORE choosing the next 30
        # records. Already acknowledged top-100 ads must not conceal new cars
        # in positions 101-300 of the existing offline research archive.
        from hunter_delivery_delta import (
            load as load_delivered, plan, acknowledge,
            delivered_ids, known_delivery_keys)
        checkpoint = load_delivered(root)
        # Count only: the public Actions log must never disclose actual IDs,
        # prices, URLs or seller information. This shows whether encrypted
        # delivery history survived between the scheduled scans.
        print(platform + ": eerder_bevestigd_in_historie=" +
              str(checkpoint_history_count(checkpoint, platform)), flush=True)
        broader_mp_feed = None
        previous_mp_signatures = None
        mp_limit = 30
        if platform == "marktplaats":
            # Preserve the original 30-card snapshot/export for existing
            # consumers. The sync selection can inspect 100 already collected
            # candidates without requesting any more marketplace pages.
            import hunter_pro_export
            mp_feed = hunter_feed.snapshot()
            hunter_pro_export.write_payload(
                target=root / "hunter_pro_export.json", feed=mp_feed)
            mp_limit = receiver_marktplaats_limit(endpoint)
            print("marktplaats: ontvangercapaciteit_per_batch=" + str(mp_limit),
                  ", doel_nieuwe_autos=50", flush=True)
            previously_sent = delivered_ids(checkpoint)
            print("marktplaats: historisch_ontvangen_ids=" +
                  str(len(previously_sent)), flush=True)
            broader_mp_feed = hunter_feed.build(
                now=now, limit=300,
                excluded_listing_ids={
                    key[len("marktplaats:"):] for key in previously_sent
                    if key.startswith("marktplaats:")
                })
            if broader_mp_feed.get("archived"):
                # An empty healthy scan can fall back to previously saved
                # research. Retain that private archive for inspection, but
                # never deliver old unverified offers as NEW AET Pro leads.
                # This changes no marketplace requests or delivery history.
                broader_mp_feed = {**broader_mp_feed, "cars": [], "count": 0}
                print("marktplaats: alleen_historisch_onderzoek; geen_nieuwe_leads",
                      flush=True)
            previous_mp_signatures = known_delivery_keys(checkpoint)
        # Always write inside the PRIVATE temp state directory. Calling
        # write(now=now) would use the module's import-time default OUTPUT,
        # which can point into the public checkout on a GitHub runner.
        combined = hunter_pro_combined.write(
            path=root / "hunter_pro_combined.json", now=now,
            mp_feed=broader_mp_feed,
            known_mp_fingerprints=previous_mp_signatures,
            mp_limit=50 if platform == "marktplaats" else mp_limit)
        persist()
        # Full research archive is not the same as newly discovered ads.
        # Compare against ONLY confirmed prior deliveries; failed syncs must
        # be retried instead of quietly losing potential opportunities.
        scoped, signatures, changes = plan(
            combined, platform, checkpoint,
            new_only=(platform == "marktplaats"))
        print(platform + ": bekeken_in_export=" + str(changes["observed"]) +
              ", nieuw=" + str(changes["new_to_delivery"]) +
              ", inhoudelijk_gewijzigd=" + str(changes["changed"]) +
              ", herhaald_overgeslagen=" + str(changes["repeated"]), flush=True)
        run_id, attempt = durable_scan_identity(env)
        # A currently deployed Worker may accept only 30 per request. Deliver
        # up to 50 ACTUALLY unseen Marktplaats ads in independent, bounded
        # batches (30+20) rather than claiming 50 while quietly truncating.
        # Each batch gets a stable id and its own durable acknowledgement.
        # On partial failure already accepted cars remain remembered; the
        # unsent tail is retried on the next scan, never marked as delivered.
        import hashlib
        candidates = scoped["candidates"]
        per_batch = mp_limit if platform == "marktplaats" else 30
        chunks = [candidates[i:i + per_batch]
                  for i in range(0, len(candidates), per_batch)] or [[]]
        total_sent = 0
        for index, chunk in enumerate(chunks):
            batch = dict(scoped)
            batch["candidates"] = chunk
            batch["count"] = len(chunk)
            batch["platform_counts"] = {
                "marktplaats": len(chunk) if platform == "marktplaats" else 0,
                "facebook": len(chunk) if platform == "facebook" else 0,
            }
            batch["revision"] = hashlib.sha256(json.dumps(
                sorted((car["id"], car["content_hash"]) for car in chunk),
                separators=(",", ":")).encode("utf-8")).hexdigest()
            # Associate the scan ID with the actual candidate set, NOT its
            # ordinal slot. After partial success a tail batch may become
            # chunk zero on retry. Reusing the old chunk-zero ID could make
            # the receiver silently treat those unsent cars as duplicates.
            chunk_id = (stable_marktplaats_batch_run_id(run_id, chunk)
                        if platform == "marktplaats" else run_id)
            package = hunter_sync.envelope(
                source, chunk_id, attempt, now=now,
                combined=batch, source_status=status)
            # Never log raw URLs, offers, credentials or Worker responses.
            answer = hunter_sync.send(package, endpoint, token)
            if answer.get("received") is not True:
                raise RuntimeError("AET Pro did not confirm receipt")
            acknowledge(root, platform,
                        {car["id"]: signatures[car["id"]] for car in chunk},
                        persist)
            total_sent += len(chunk)
        print(platform + ": synced; nieuwe_kandidaten=" + str(total_sent) +
              ", batches=" + str(len(chunks)), flush=True)
        return "synced"
    finally:
        os.chdir(cwd)
        (hunter_feed.ROOT, hunter_pro_combined.OUTPUT,
         hunter_sync.ROOT, facebook_hunter.ROOT) = previous_paths
