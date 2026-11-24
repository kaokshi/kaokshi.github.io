"""Bounded cloud intake cycle. No inference, stage execution or publishing.

A private CAS checkpoint lets successive short-lived runners scan large queues.
The caller must serialize cycles; CAS detects accidental concurrent writers.
"""
import json
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intake_client import drain
from src.editorial_zen_cloud import CloudCore, GitHubStore, STATE
from src.editorial_zen_core import CoreBlocked

CHECKPOINT = "intake-runner.json"
CURSOR = re.compile(r"production:index:\d{15}:[-a-zA-Z0-9:]{1,120}")


def validate_checkpoint(raw):
    if raw is None:
        return None
    if len(raw) > 2048:
        raise CoreBlocked("intake_checkpoint_invalid")
    try:
        row = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CoreBlocked("intake_checkpoint_invalid") from None
    if (not isinstance(row, dict) or set(row) != {"schema", "cursor"}
            or row["schema"] != "zen-core/intake-runner-v1"
            or row["cursor"] is not None and (not isinstance(row["cursor"], str) or not CURSOR.fullmatch(row["cursor"]))):
        raise CoreBlocked("intake_checkpoint_invalid")
    return row["cursor"]


def cycle(store, endpoint, secret, *, consume=drain, max_pages=10):
    # Refuse a missing ledger, and refuse execution-enabled config even if queue is empty.
    if not isinstance(max_pages, int) or not 1 <= max_pages <= 10:
        raise CoreBlocked("invalid_intake_page_limit")
    ledger, _ = store.read(STATE)
    if ledger is None:
        raise CoreBlocked("existing_cloud_ledger_required")
    core = CloudCore(store)
    snapshot = core.access(lambda c: c.snapshot())
    if (snapshot.get("schema") != "zen-core/v1" or snapshot.get("executionMode") != "intents_only"
            or any(snapshot.get(k) is not False for k in ("executionEnabled", "publicationAuthorized", "rssEnabled"))):
        raise CoreBlocked("intake_runner_not_safe")
    raw, previous = store.read(CHECKPOINT)
    cursor = validate_checkpoint(raw)
    result = consume(core, endpoint, secret, max_pages=max_pages, cursor=cursor)
    nxt = result["nextCursor"]
    if nxt is not None and (not isinstance(nxt, str) or not CURSOR.fullmatch(nxt) or cursor is not None and nxt <= cursor):
        raise CoreBlocked("intake_checkpoint_invalid")
    if nxt != cursor:
        payload = json.dumps({"schema": "zen-core/intake-runner-v1", "cursor": nxt}, separators=(",", ":")).encode()
        store.write(CHECKPOINT, payload, previous)
    return {"delivered": result["delivered"], "skipped": result["skipped"], "morePages": nxt is not None,
            "executionEnabled": False, "publicationAuthorized": False, "rssEnabled": False}


def main():
    from src.editorial_zen_transport import bridge_opener
    from urllib.request import Request
    endpoint = os.environ["ZEN_CORE_INTAKE_ENDPOINT"]
    # Pin the known existing Worker; credentials never go to arbitrary hosts.
    if endpoint != "https://zen-editorial-control.kvasrapr.workers.dev/internal/zen-core/intake":
        raise CoreBlocked("intake_endpoint_mismatch")
    secret = os.environ["ZEN_CORE_INTAKE_SECRET"]
    if not re.fullmatch(r"[a-fA-F0-9]{64}", secret):
        raise CoreBlocked("invalid_intake_secret")
    result = cycle(GitHubStore(os.environ["EDITORIAL_PRIVATE_TOKEN"]), endpoint, secret)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never emit URLs, credentials, private job content or provider responses.
        print("INTAKE_RUNNER_BLOCKED_" + type(exc).__name__)
        sys.exit(1)
