#!/usr/bin/env python3
"""Zen AI Editorial Agent v1: read-only, provenance-bound review/escalation router.

This routes evidence produced by the existing check workflow. The separate
editorial_revision command performs draft revisions and prepares handoff packages.
"""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
from editorial_ai_check import MODEL_A, REQUIRED_CHECKS, REVIEW_THRESHOLD, extract_text

THRESHOLD = REVIEW_THRESHOLD
ACTIVE = ("legends", "science")
MAX_AGENT = 2
MAX_ASSISTANT = 1

def article_digest(article):
    raw = extract_text(article.get("body_html", ""))
    if not raw:
        raise ValueError("Article must have actual text")
    return sha256(raw.encode("utf-8")).hexdigest()

def validate_ai_report(article, report):
    if not isinstance(report, dict):
        return None
    if report.get("article_slug") != article.get("slug"):
        return None
    if report.get("text_sha256") != article_digest(article):
        return None
    if report.get("all_checks_completed") is not True:
        return None
    checks = report.get("checks")
    if not isinstance(checks, dict) or not all(
        isinstance(checks.get(name), dict) and checks[name].get("status") == "completed"
        for name in REQUIRED_CHECKS
    ):
        return None
    threshold = report.get("editorial_threshold")
    score = threshold.get("model_a_index_0_100") if isinstance(threshold, dict) else None
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100:
        return None
    return float(score)

def validate_history(history):
    if not isinstance(history, list) or len(history) > MAX_AGENT + MAX_ASSISTANT:
        raise ValueError("Invalid revision history length")
    stages = [x.get("stage") for x in history if isinstance(x, dict)]
    if any(stage not in ("agent", "assistant") for stage in stages):
        raise ValueError("Invalid history stage")
    if stages != ["agent"] * min(stages.count("agent"), MAX_AGENT) + ["assistant"] * stages.count("assistant"):
        raise ValueError("Revision stages out of order")
    if stages.count("agent") > MAX_AGENT or stages.count("assistant") > MAX_ASSISTANT:
        raise ValueError("Attempt limit exceeded")
    digests = set()
    for entry in history:
        before, after = entry.get("before_text_sha256"), entry.get("after_text_sha256")
        if not (isinstance(before, str) and isinstance(after, str) and len(before)==64 and len(after)==64):
            raise ValueError("Missing immutable pre/post revision hashes")
        if before == after:
            raise ValueError("Unchanged text cannot count as a revision attempt")
        if after in digests:
            raise ValueError("Repeated text version")
        if not isinstance(entry.get("ai_report_ref"), str) or not entry["ai_report_ref"].strip():
            raise ValueError("Missing fresh AI report reference")
        if entry.get("all_checks_rerun") is not True:
            raise ValueError("Changed text requires all fresh model checks")
        if not isinstance(entry.get("substantive_changes"), list) or not entry["substantive_changes"] or not all(isinstance(x,str) and x.strip() for x in entry["substantive_changes"]):
            raise ValueError("Each attempt needs real, documented editorial changes")
        if digests and before not in digests:
            raise ValueError("History revisions are not chained")
        digests.add(after)
    return stages

def route(article, report, history=None, substantive_issues=None):
    """Fail closed; a score alone never proves bad writing."""
    history = history or []
    stages = validate_history(history)
    current = article_digest(article)
    common = {"schema": "zen-editorial-agent-routing-v1",
              "slug": article.get("slug"), "channel": article.get("channel"),
              "text_sha256": current, "agent_attempts": stages.count("agent"),
              "assistant_attempts": stages.count("assistant"),
              "index_is_ai_authorship_percent": False,
              "publication_authorized": False, "rss_delivery_authorized": False}
    if article.get("channel") not in ACTIVE:
        return {**common, "decision": "paused_channel", "index": None}
    if stages and history[-1]["after_text_sha256"] != current:
        return {**common, "decision": "history_text_digest_mismatch", "index": None}
    index = validate_ai_report(article, report)
    if index is None:
        return {**common, "decision": "incomplete_or_stale_checks", "index": None}
    common["index"] = index
    common["review_if_above"] = THRESHOLD
    if index <= THRESHOLD:
        return {**common, "decision": "other_editorial_checks_required"}
    if substantive_issues is None:
        return {**common, "decision": "inspect_for_substantive_issues"}
    if not isinstance(substantive_issues, list) or any(not isinstance(x, str) or not x.strip() for x in substantive_issues):
        raise ValueError("Invalid substantive issues")
    if not substantive_issues:
        return {**common, "decision": "high_index_without_verified_defect_owner_review"}
    common["documented_issues"] = substantive_issues
    if stages.count("agent") < MAX_AGENT:
        return {**common, "decision": "agent_targeted_revision",
                "next_agent_attempt": stages.count("agent")+1,
                "requires_new_text_and_fresh_all_checks": True}
    if stages.count("assistant") < MAX_ASSISTANT:
        return {**common, "decision": "chatgpt_one_revision_handoff",
                "transport_status": "not_connected", "requires_new_text_and_fresh_all_checks": True}
    return {**common, "decision": "owner_review_final",
            "transport_status": "not_connected",
            "reason": "two_agent_and_one_chatgpt_attempts_exhausted"}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--article",required=True)
    p.add_argument("--ai-report",required=True)
    p.add_argument("--history")
    p.add_argument("--issues")
    p.add_argument("--output",default="editorial-agent-routing")
    args=p.parse_args()
    article=json.loads(Path(args.article).read_text("utf-8"))
    report=json.loads(Path(args.ai_report).read_text("utf-8"))
    history=json.loads(Path(args.history).read_text("utf-8")) if args.history else []
    issues=json.loads(Path(args.issues).read_text("utf-8")) if args.issues else None
    result=route(article,report,history,issues)
    destination=Path(args.output)
    destination.mkdir(parents=True,exist_ok=True)
    (destination / (str(article.get("slug"))+".json")).write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"slug":result["slug"],"decision":result["decision"],"index":result["index"]},ensure_ascii=False))

if __name__=="__main__":
    main()
