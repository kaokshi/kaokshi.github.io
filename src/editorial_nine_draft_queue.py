#!/usr/bin/env python3
"""Nine-draft, three-channel preparation queue with idempotent private state.

Accepts a reviewed nine-topic daily plan. Never fabricates topics or sources;
never publishes, uploads article text, or silently marks failed tasks complete.
"""
import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys

from editorial_two_channel_pilot import endpoint_ready, dossier_summary, run_one
from editorial_writer import private_path, topic_key

CHANNELS = ("legends", "science", "cinema")
DAILY_COUNT = 3
DAY = re.compile(r"^20\d\d-\d\d-\d\d$")


def validate_plan(plan):
    if not isinstance(plan, dict) or not DAY.fullmatch(str(plan.get("date", ""))):
        raise ValueError("Plan needs an ISO calendar date")
    date.fromisoformat(plan["date"])
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != len(CHANNELS)*DAILY_COUNT:
        raise ValueError("Exactly nine planned articles required: three per channel")
    seen = set()
    count = {c: 0 for c in CHANNELS}
    cleaned = []
    for entry in tasks:
        if not isinstance(entry, dict):
            raise ValueError("Each task must be an object")
        channel, topic = entry.get("channel"), entry.get("topic")
        if channel not in count or not isinstance(topic, str) or not (8 <= len(topic.strip()) <= 180):
            raise ValueError("Unknown channel or invalid topic")
        key = topic_key(topic)
        if not key or key in seen:
            raise ValueError("Duplicate or empty topic")
        seen.add(key)
        count[channel] += 1
        path = entry.get("source_documents")
        query = entry.get("research_query")
        if channel == "science" and not (
            isinstance(query, str) and query.strip() or
            isinstance(path, str) and path.strip()
        ):
            raise ValueError("Science topic needs research_query or source_documents")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            raise ValueError("Invalid source documents path")
        if query is not None and (not isinstance(query, str) or not query.strip()):
            raise ValueError("Invalid research query")
        cleaned.append({"channel": channel, "topic": topic.strip(),
                        "research_query": query, "source_documents": path,
                        "task_id": channel + "-" + sha256(key.encode("utf-8")).hexdigest()[:16]})
    if any(n != DAILY_COUNT for n in count.values()):
        raise ValueError("Must have exactly three tasks per channel")
    return cleaned


def report_status(workspace, task):
    row = dossier_summary(workspace, task["channel"], task["topic"])
    return {"task_id": task["task_id"],"channel":task["channel"],
            "topic":task["topic"],**row}


def execute(plan, workspace, *, status_only=False, health_check=endpoint_ready,
            runner=run_one):
    tasks=validate_plan(plan)
    workspace=private_path(Path(workspace))
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    backend=os.environ.get("ZEN_LLM_BACKEND","llama.cpp")
    url=os.environ.get("ZEN_LLM_URL",
      "http://127.0.0.1:11434" if backend=="ollama" else "http://127.0.0.1:8093")
    health={"status":"not_checked"} if status_only else health_check(url,backend=backend)
    records=[]
    for task in tasks:
        row=report_status(workspace,task)
        if status_only or health["status"]!="ready":
            row["execution"]={"status":"not_started","reason":"status_only" if status_only else "model_not_ready"}
        else:
            src=task["source_documents"]
            source_path=Path(src).expanduser().resolve() if src is not None else None
            if source_path is not None and (not source_path.is_file() or not source_path.is_relative_to(workspace)):
                row["execution"]={"status":"blocked","reason":"source_documents_missing_or_not_private"}
            else:
                try:
                    outcome=runner(workspace,task["channel"],task["topic"],
                                   research_query=task["research_query"],source_documents=source_path)
                    row=report_status(workspace,task)
                    row["execution"]=outcome["execution"]
                except (OSError,TypeError,ValueError,KeyError) as exc:
                    row["execution"]={"status":"error","reason":type(exc).__name__}
        records.append(row)
    return {"schema":"zen-nine-draft-queue-v1","date":plan["date"],
            "checked_at":datetime.now(timezone.utc).isoformat(),
            "model_health":health,"requested":9,
            "per_channel":{c:sum(r["channel"]==c for r in records) for c in CHANNELS},
            "ready_for_owner":sum(bool(r["article_ready_for_owner"]) for r in records),
            "completed_for_publication":0,
            "publication_authorized":False,"rss_authorized":False,
            "transport_status":"not_connected","tasks":records}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--plan",type=Path,required=True)
    ap.add_argument("--workspace",type=Path,required=True)
    ap.add_argument("--status-only",action="store_true")
    args=ap.parse_args()
    try:
        workspace=private_path(args.workspace)
        plan=json.loads(args.plan.read_text(encoding="utf-8"))
        report=execute(plan,workspace,status_only=args.status_only)
        output=workspace/("nine-draft-status-"+report["date"]+".json")
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        os.chmod(output,0o600)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        ap.error(str(exc))
    print(json.dumps({"date":report["date"],"requested":report["requested"],
                      "by_channel":report["per_channel"],
                      "ready_for_owner":report["ready_for_owner"],
                      "model_health":report["model_health"]["status"],
                      "report":str(output)},ensure_ascii=False))
    return 0 if args.status_only or (
        report["model_health"]["status"]=="ready" and
        all(row["execution"].get("exit_code")==0 for row in report["tasks"])
    ) else 2


if __name__=="__main__":
    raise SystemExit(main())
