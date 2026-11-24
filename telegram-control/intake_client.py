"""Intake-only client for the existing editorial_zen_core.Core; no paid calls.
Run from a private, persistent executor host with the PR85 Core installed.
"""
import argparse
import json
import os
import re
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def accept(core, envelope, expected_id):
    if (not isinstance(envelope, dict)
            or envelope.get("schema") != "zen-core/intake-v1"
            or envelope.get("mode") != "intents_only"
            or any(envelope.get(k) is not False for k in
                   ("executionEnabled", "publicationAuthorized", "rssEnabled"))):
        raise ValueError("unsafe_intake_envelope")
    intent = envelope.get("intent")
    if not isinstance(intent, dict) or set(intent) != {"id", "plan", "releaseAt", "mode"} or intent["id"] != expected_id:
        raise ValueError("intent_id_or_schema_mismatch")
    if intent["mode"] not in ("manual", "semi_autonomous", "autonomous"):
        raise ValueError("invalid_execution_mode")
    if any(core.policy.get(k) is not False for k in ("execution_enabled", "publication_enabled", "rss_enabled")):
        raise ValueError("core_not_in_intake_only_mode")
    transition = envelope.get("modeTransition")
    if transition is not None and (not isinstance(transition, dict) or set(transition) != {"mode", "revision"}
            or transition["mode"] not in ("manual", "semi_autonomous", "autonomous")
            or type(transition["revision"]) is not int or not 0 < transition["revision"] <= 2**53-1):
        raise ValueError("invalid_mode_transition")
    job_id = core.submit(intent)
    if transition is not None:
        core.apply_mode(job_id, transition["mode"], transition["revision"])
    return job_id


def pull(endpoint, secret, job_id):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/internal/zen-core/intake"):
        raise ValueError("invalid_intake_endpoint")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", secret or ""):
        raise ValueError("missing_intake_secret")
    if not re.fullmatch(r"[-a-zA-Z0-9:]{1,120}", job_id):
        raise ValueError("invalid_job_id")
    request = urllib.request.Request(endpoint, data=json.dumps({"id": job_id}).encode(),
                                     headers={"Authorization": "Bearer " + secret,
                                              "Content-Type": "application/json"}, method="POST")
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        data = response.read(16385)
        if len(data) > 16384:
            raise ValueError("intake_too_large")
        return json.loads(data)


def build_receipt(core, job_id, observed_at=None):
    snapshot = core.snapshot()
    if snapshot.get("schema") != "zen-core/v1" or snapshot.get("executionMode") != "intents_only" or any(
            snapshot.get(k) is not False for k in ("executionEnabled", "publicationAuthorized", "rssEnabled")):
        raise ValueError("unsafe_core_snapshot")
    jobs = [j for j in snapshot["jobs"] if j["id"] == job_id]
    if len(jobs) != 1:
        raise ValueError("job_missing")
    return {"schema": "zen-core/receipt-v1", "jobId": job_id,
            "observedAt": int(time.time() * 1000) if observed_at is None else observed_at,
            "modeRevision": jobs[0]["modeRevision"], "effectiveMode": jobs[0]["mode"],
            "articles": [{"id": a["id"], "meteredRub": a["meteredRub"],
                          "stages": [{"id": s["id"], "status": s["status"]} for s in a["stages"]]}
                         for a in jobs[0]["articles"]],
            "budget": {k: snapshot["budget"][k] for k in (
                "day", "timezone", "mainDailyRub", "reserveDailyRub", "mainRemainingRub",
                "reserveRemainingRub", "meteredRub", "unsettledRequest", "overrun",
                "rollingStart", "rollingCapRub", "rollingRemainingRub", "rollingMeteredRub", "rollingOverrun")},
            "executionEnabled": False, "publicationAuthorized": False, "rssEnabled": False}


def push_receipt(endpoint, secret, receipt):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/internal/zen-core/intake"):
        raise ValueError("invalid_intake_endpoint")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", secret or ""):
        raise ValueError("missing_intake_secret")
    request = urllib.request.Request(endpoint.removesuffix("/intake") + "/receipt",
                                     data=json.dumps(receipt).encode(),
                                     headers={"Authorization": "Bearer " + secret,
                                              "Content-Type": "application/json"}, method="POST")
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(1025)
        if len(raw) > 1024 or json.loads(raw).get("accepted") is not True:
            raise ValueError("receipt_not_confirmed")


def discover(endpoint, secret, cursor=None):
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != "/internal/zen-core/intake"):
        raise ValueError("invalid_intake_endpoint")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", secret or ""):
        raise ValueError("missing_intake_secret")
    request = urllib.request.Request(endpoint.removesuffix("/intake") + "/queue",
                                     data=json.dumps({"cursor": cursor}).encode(),
                                     headers={"Authorization": "Bearer " + secret,
                                              "Content-Type": "application/json"}, method="POST")
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(8193)
        if len(raw) > 8192:
            raise ValueError("queue_response_too_large")
        row = json.loads(raw)
    if (not isinstance(row, dict) or row.get("schema") != "zen-core/queue-v1"
            or row.get("mode") != "intents_only" or type(row.get("stopped")) is not bool
            or any(row.get(k) is not False for k in ("executionEnabled", "publicationAuthorized", "rssEnabled"))):
        raise ValueError("unsafe_queue_response")
    ids = row.get("ids")
    nxt = row.get("nextCursor")
    if (not isinstance(ids, list) or len(ids) > 20
            or any(not isinstance(i, str) or not re.fullmatch(r"[-a-zA-Z0-9:]{1,120}", i) for i in ids)
            or len(set(ids)) != len(ids) or row["stopped"] and (ids or nxt is not None)
            or nxt is not None and (not isinstance(nxt, str)
                or not re.fullmatch(r"production:index:\d{15}:[-a-zA-Z0-9:]{1,120}", nxt))):
        raise ValueError("invalid_queue_response")
    return ids, nxt


def drain(core, endpoint, secret, *, max_pages=10, cursor=None, fetch_page=discover,
          fetch_intent=pull, send_receipt=push_receipt):
    # One bounded polling cycle. Only durable intent submission and receipt delivery.
    delivered, skipped = 0, 0
    seen = set()
    for _ in range(max_pages):
        ids, nxt = fetch_page(endpoint, secret, cursor)
        for job_id in ids:
            if job_id in seen:
                continue
            seen.add(job_id)
            try:
                envelope = fetch_intent(endpoint, secret, job_id)
            except HTTPError as exc:
                if exc.code in (404, 409):
                    skipped += 1
                    continue
                raise
            core.access(lambda c: accept(c, envelope, job_id), change=True)
            receipt = core.access(lambda c: build_receipt(c, job_id))
            try:
                send_receipt(endpoint, secret, receipt)
            except HTTPError as exc:
                if exc.code in (404, 409):
                    skipped += 1
                    continue
                raise
            delivered += 1
        if nxt is None:
            return {"delivered": delivered, "skipped": skipped, "nextCursor": None}
        if cursor is not None and nxt <= cursor:
            raise ValueError("queue_cursor_not_advancing")
        cursor = nxt
    return {"delivered": delivered, "skipped": skipped, "nextCursor": cursor}


def main():
    from src.editorial_zen_cloud import CloudCore, GitHubStore
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job")
    group.add_argument("--queue", action="store_true")
    parser.add_argument("--cursor")
    parser.add_argument("--report", action="store_true", help="Send informational snapshot after intake")
    args = parser.parse_args()
    core = CloudCore(GitHubStore(os.environ.get("EDITORIAL_PRIVATE_TOKEN")))
    if args.queue:
        result = drain(core, args.endpoint, os.environ.get("ZEN_CORE_INTAKE_SECRET"), cursor=args.cursor)
        print(json.dumps({**result, "mode": "intents_only", "executed": False, "publicationAuthorized": False}))
        return
    envelope = pull(args.endpoint, os.environ.get("ZEN_CORE_INTAKE_SECRET"), args.job)
    job_id = core.access(lambda c: accept(c, envelope, args.job), change=True)
    if args.report:
        push_receipt(args.endpoint, os.environ.get("ZEN_CORE_INTAKE_SECRET"), core.access(lambda c: build_receipt(c, job_id)))
    print(json.dumps({"id": job_id, "mode": "intents_only", "receiptConfirmed": args.report,
                      "executed": False, "publicationAuthorized": False}))


if __name__ == "__main__":
    main()
