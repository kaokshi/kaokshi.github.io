"""Bounded integration cycle. Intake can run without inference permissions."""
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from document_delivery import deliver_documents
from intake_client import build_receipt, drain, push_receipt
from intake_runner import CHECKPOINT, CURSOR, validate_checkpoint
from src.editorial_memory import digest
from src.editorial_zen_cloud import CloudCore, GitHubStore, STATE
from src.editorial_zen_control import configured_account, configured_guard
from src.editorial_zen_core import CoreBlocked
from src.editorial_zen_dispatch import dispatch_one
from src.editorial_zen_executor import execute
from src.editorial_zen_recovery import AUTO_RETRY
from src.editorial_routerai import UnsafeToTest
from src.editorial_http import Blocked

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


def dispatch_cycle(core, endpoint, secret, token, *, guard, account, job_scope=None,
                   advance=dispatch_one, deliver=push_receipt):
    recover_recorded_response(core, account)
    jobs = core.access(lambda c: c.snapshot())["jobs"]
    candidates = sorted(a["id"] for j in jobs if not job_scope or j["id"] == job_scope for a in j["articles"]
                        if any(s["status"] != "passed" for s in a["stages"]))
    if not candidates:
        return {"attempted": 0, "receipts": 0}
    raw, rev = core.store.read(DISPATCH_CURSOR)
    last = json.loads(raw)["after"] if raw else ""
    if not isinstance(last, str) or last and not re.fullmatch(r"[-a-zA-Z0-9:]{1,120}/(?:cinema|legends|science)/[1-9]", last):
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


def dispatch_passes(core, endpoint, secret, token, *, guard, account, job_scope, count, deliver):
    """Run bounded passes for one explicit pilot, with the same per-stage controls."""
    totals = {"attempted": 0, "receipts": 0}
    deadline = monotonic() + 25 * 60
    for number in range(count):
        started = monotonic()
        result = dispatch_cycle(core, endpoint, secret, token, guard=guard,
                                account=account, job_scope=job_scope)
        for field in totals:
            totals[field] += result[field]
        if count > 1:
            print(json.dumps({"pass": number + 1, "elapsedSeconds": round(monotonic() - started, 1),
                              **result}), flush=True)
        snapshot = core.access(lambda c: c.snapshot())
        deliver(snapshot)
        if count == 1 or not result["attempted"] or monotonic() >= deadline:
            break
        budget = snapshot["budget"]
        if budget["unsettledRequest"] or budget["overrun"] or budget["rollingOverrun"]:
            break
        job = next(job for job in snapshot["jobs"] if job["id"] == job_scope)
        if job["mode"] != "autonomous" or not any(
                next((stage["status"] for stage in article["stages"] if stage["status"] != "passed"), None)
                in ("not_started", "released") for article in job["articles"]):
            break
    return totals


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
    pilot = os.getenv("ZEN_CORE_PILOT_JOB", "")
    if pilot:
        if not production or not re.fullmatch(r"agent:[a-f0-9-]{36}", pilot):
            raise CoreBlocked("invalid_production_pilot_job")
        jobs = core.access(lambda c: c.snapshot())["jobs"]
        job = next((job for job in jobs if job["id"] == pilot), None)
        if job is None or {row["channel"]: row["count"] for row in job["items"]} != {
                "cinema": 2, "legends": 2, "science": 2}:
            raise CoreBlocked("confirmed_six_article_pilot_required")
    count = os.getenv("ZEN_CORE_PILOT_PASSES", "1")
    if not re.fullmatch(r"[1-8]", count) or (count != "1" and not pilot):
        raise CoreBlocked("invalid_production_pilot_passes")
    delivery_job = pilot or os.getenv("ZEN_CORE_TELEGRAM_DOCX_JOB", "")
    documents = {"sent": 0, "unconfirmed": 0}

    def deliver(snapshot):
        job = next((job for job in snapshot["jobs"] if job["id"] == delivery_job), None)
        if job:
            result = deliver_documents(core.store, job["articles"], delivery_job,
                                       os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_OWNER_ID"))
            documents["sent"] += result["sent"]
            documents["unconfirmed"] = result["unconfirmed"]

    steps = dispatch_passes(core, endpoint, secret, os.environ["ROUTERAI_API_KEY"],
                            guard=configured_guard(), account=configured_account(), job_scope=pilot,
                            count=int(count), deliver=deliver) if production else {}
    if production and delivery_job:
        steps["telegramDocuments"] = documents
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
