"""Bounded integration cycle. Intake can run without inference permissions."""
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intake_client import build_receipt, drain, push_receipt
from intake_runner import CHECKPOINT, CURSOR, validate_checkpoint
from src.editorial_memory import digest
from src.editorial_zen_cloud import CloudCore, GitHubStore, STATE
from src.editorial_zen_control import configured_account, configured_guard
from src.editorial_zen_core import CoreBlocked
from src.editorial_zen_dispatch import dispatch_one
from src.editorial_zen_executor import execute
from src.editorial_zen_recovery import AUTO_RETRY
from src.editorial_routerai_smoke import UnsafeToTest
from src.editorial_providers import Blocked

DISPATCH_CURSOR = "dispatch-cursor.json"
ENDPOINT = "https://zen-editorial-control.kvasrapr.workers.dev/internal/zen-core/intake"


def accept_cycle(core, endpoint, secret, production, *, consume=drain):
    raw, previous = core.store.read(CHECKPOINT)
    cursor = validate_checkpoint(raw)
    result = consume(core, endpoint, secret, max_pages=10, cursor=cursor, production=production)
    nxt = result["nextCursor"]
    if nxt is not None and (not isinstance(nxt, str) or not CURSOR.fullmatch(nxt)
                            or cursor is not None and nxt <= cursor):
        raise CoreBlocked("intake_checkpoint_invalid")
    if nxt != cursor:
        core.store.write(CHECKPOINT, json.dumps({"schema": "zen-core/intake-runner-v1",
                                                "cursor": nxt}).encode(), previous)
    return result


def recover_recorded_response(core, account):
    hold = account("status").get("production")
    if not hold:
        return
    if not isinstance(hold, dict):
        raise CoreBlocked("account_hold_invalid")
    op = hold.get("operation")
    if not isinstance(op, str) or not re.fullmatch(r"[-a-zA-Z0-9:_/]{1,180}", op):
        raise CoreBlocked("account_hold_invalid")
    if "/" not in op:
        raise CoreBlocked("account_hold_invalid")
    article, stage = op.rsplit("/", 1)
    if article.split("/")[0] != hold.get("id"):
        raise CoreBlocked("account_hold_invalid")
    raw, _ = core.store.read("articles/" + digest(article) + "/requests/" + stage + ".json")
    if raw is None:
        # A provider charge might have occurred. Only an owner reconciliation can clear it.
        raise CoreBlocked("financial_reconciliation_required")
    response = json.loads(raw)
    actual = response["answer"]["usage"]["cost"]
    core.access(lambda c: c.settle(op, actual), change=True)
    account("close", hold["id"], op, "settled")


def dispatch_cycle(core, endpoint, secret, token, *, guard, account, advance=dispatch_one, deliver=push_receipt):
    recover_recorded_response(core, account)
    jobs = core.access(lambda c: c.snapshot())["jobs"]
    candidates = sorted(a["id"] for j in jobs for a in j["articles"]
                        if any(s["status"] != "passed" for s in a["stages"]))
    if not candidates:
        return {"attempted": 0, "receipts": 0}
    raw, rev = core.store.read(DISPATCH_CURSOR)
    last = json.loads(raw)["after"] if raw else ""
    if not isinstance(last, str) or last and not re.fullmatch(r"[-a-zA-Z0-9:]{1,120}/(?:legends|science)/[1-9]", last):
        raise CoreBlocked("dispatch_cursor_invalid")
    ordered = [a for a in candidates if a > last] + [a for a in candidates if a <= last]
    attempted = receipts = 0
    for article in ordered[:18]:
        job_id = article.split("/", 1)[0]
        try:
            result = advance(core, article, token, guard=guard, execute=execute)
            reason = result.get("reason")
            attempted += result.get("status") == "attempt_finished"
        except (CoreBlocked, UnsafeToTest, Blocked) as exc:
            if str(exc) == "scheduled_job_not_due":
                continue
            reason = str(exc)
            if reason in AUTO_RETRY:
                def mark(c):
                    _, stage = c.article_work(article)
                    if not stage:
                        return
                    previous = json.loads(c.check_stage(article, stage).get(stage, "{}"))
                    if previous.get("status") not in ("blocked", "failed") or previous.get("reason") in AUTO_RETRY:
                        c.stage_result(article, stage, {"status": "blocked", "reason": reason,
                                                       "checks": previous.get("checks", {})})
                core.access(mark, change=True)
            attempted += 1
        receipt = core.access(lambda c: build_receipt(c, job_id, production=True))
        if isinstance(reason, str) and re.fullmatch(r"[a-z0-9_]{1,120}", reason):
            receipt["dispatchReason"] = reason
        try:
            deliver(endpoint, secret, receipt)
            receipts += 1
        except HTTPError as exc:
            if exc.code not in (404, 409):
                raise
        core.store.write(DISPATCH_CURSOR, json.dumps({"after": article}).encode(), rev)
        _, rev = core.store.read(DISPATCH_CURSOR)
        if attempted >= 2:
            break
    return {"attempted": attempted, "receipts": receipts}


def main():
    endpoint = os.getenv("ZEN_CORE_INTAKE_ENDPOINT")
    secret = os.getenv("ZEN_CORE_INTAKE_SECRET", "")
    if endpoint != ENDPOINT or not re.fullmatch(r"[a-fA-F0-9]{64}", secret):
        raise CoreBlocked("invalid_production_runner_configuration")
    core = CloudCore(GitHubStore(os.environ["EDITORIAL_PRIVATE_TOKEN"]))
    if core.store.read(STATE)[0] is None:
        raise CoreBlocked("existing_cloud_ledger_required")
    snapshot = core.access(lambda c: c.snapshot())
    production = os.getenv("ZEN_CORE_PRODUCTION_ENABLED") == "true"
    if (snapshot["publicationAuthorized"] is not False or snapshot["rssEnabled"] is not False
            or snapshot["executionEnabled"] is not production):
        raise CoreBlocked("execution_gate_mismatch")
    intake = accept_cycle(core, endpoint, secret, production)
    steps = dispatch_cycle(core, endpoint, secret, os.environ["ROUTERAI_API_KEY"],
                           guard=configured_guard(), account=configured_account()) if production else {}
    print(json.dumps({"intakeDelivered": intake["delivered"], **steps,
                      "executionEnabled": production, "publicationAuthorized": False, "rssEnabled": False}))


if __name__ == "__main__":
    try:
        main()
    except (CoreBlocked, UnsafeToTest, Blocked, HTTPError, OSError, KeyError, ValueError) as exc:
        code = str(exc)
        print("ZEN_CYCLE_BLOCKED_" + (code if re.fullmatch(r"[a-z0-9_]{1,120}", code)
                                     else type(exc).__name__))
        sys.exit(1)
