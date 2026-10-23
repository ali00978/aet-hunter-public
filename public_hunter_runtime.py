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
            healthy = status.get("scan_health") == "ok"
        else:
            from public_apify_collector import collect
            status = collect(root, env, persist)
            healthy = status.get("status") == "ok"
        print(platform + ": healthy=" + str(healthy))
        if not healthy:
            return "unhealthy"
        now = datetime.now(timezone.utc)
        if platform == "marktplaats":
            # Mirror the original Hunter: retain both the app feed and
            # the versioned Marktplaats export before the combined AET Pro batch.
            import hunter_pro_export
            mp_feed = hunter_feed.snapshot()
            hunter_pro_export.write_payload(
                target=root / "hunter_pro_export.json", feed=mp_feed)
        # Always write inside the PRIVATE temp state directory. Calling
        # write(now=now) would use the module's import-time default OUTPUT,
        # which can point into the public checkout on a GitHub runner.
        combined = hunter_pro_combined.write(
            path=root / "hunter_pro_combined.json", now=now)
        persist()
        run_id = str(time.time_ns())
        package = hunter_sync.envelope(
            source, run_id, "1", now=now,
            combined=combined, source_status=status)
        # No candidate URLs, details, credentials, or endpoint responses logged.
        answer = hunter_sync.send(package, endpoint, token)
        if answer.get("received") is not True:
            raise RuntimeError("AET Pro did not confirm receipt")
        print(platform + ": synced; candidates=" +
              str(len(package["scan"]["candidate_ids"])))
        return "synced"
    finally:
        os.chdir(cwd)
