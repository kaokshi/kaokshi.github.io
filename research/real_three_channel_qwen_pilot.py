#!/usr/bin/env python3
"""Real three-channel autonomous editorial smoke test; private drafts only.

The Qwen planner chooses topics without owner input. Every step and missing
asset is reported accurately; incomplete text is never called publish-ready.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import monotonic

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_daily_topic_plan import create_plan
from editorial_two_channel_pilot import run_one, dossier_summary, endpoint_ready
from editorial_writer import command_provider, private_path
from editorial_commons_images import search as commons_search
from editorial_encrypted_review import save_encrypted_review

CHANNELS=("legends","science","cinema")


def run(root, planner=create_plan, writer=run_one, image_search=commons_search):
    root=private_path(Path(root))
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    date=datetime.now(timezone.utc).date().isoformat()
    manifest={"schema":"zen-three-channel-real-pilot-v1",
              "date":date,"mode":"real_qwen_agent_autonomously_selects_topics",
              "requested":3,"generated":0,"text_checks_complete":0,
              "articles_with_approved_images":0,"fully_ready":0,
              "owner_delivery":"not_connected","publication_authorized":False,
              "rss_authorized":False,"channels":[], "image_rights_review_completed":False}
    try:
        plan=planner(date,root,command_provider(ROOT/"src/editorial_local_llm.py"),per_channel=1)
        manifest["planning"] = "validated_three_channel_topics"
    except (ValueError,KeyError,TypeError) as exc:
        # Only an error category is public. No owner prompts, topic names,
        # untrusted source text or model responses leave this private runner.
        manifest.update(status="planner_validation_failed",
                        error_type=type(exc).__name__,
                        error_code=(str(exc).split(":", 2)[0]
                                    if str(exc).startswith("planner_channel_incomplete:")
                                    else "planner_invalid_or_duplicate_topics"),
                        failed_channel=(str(exc).split(":")[1]
                                        if str(exc).startswith("planner_channel_incomplete:")
                                        else None))
        return manifest
    except Exception as exc:
        manifest.update(status="planner_provider_failed",error_type=type(exc).__name__)
        return manifest
    selected=[next(task for task in plan["tasks"] if task["channel"]==channel)
              for channel in CHANNELS]
    for task in selected:
        ch,topic=task["channel"],task["topic"]
        row={"channel":ch,"topic_sha256":__import__("hashlib").sha256(topic.encode()).hexdigest(),
             "article_generated":False,"text_checks_complete":False,
             "approved_images":0,"image_status":"not_checked",
             "publication_authorized":False}
        try:
            outcome=writer(root,ch,topic,research_query=task.get("research_query"))
            summary=dossier_summary(root,ch,topic)
            row.update(status=summary.get("status"),chars=summary.get("draft_chars_without_whitespace"),
                       length_ok=summary.get("length_ok"),draft_sha256=summary.get("draft_sha256"),
                       text_checks_complete=bool(summary.get("last_checks_completed")),
                       article_generated=bool(summary.get("draft_sha256")),
                       agent_revision_count=max(0,int(summary.get("versions") or 1)-1),
                       pending=summary.get("manual_checks_pending",[]),
                       execution=outcome.get("execution",{}).get("pipeline_status"),
                       case_dir=summary.get("case_dir"))
            if row["article_generated"]:
                query=topic[:110]
                if ch=="science":
                    query=(task.get("research_query") or topic)[:110]
                try:
                    candidates=image_search(query,limit=12)
                    row["image_status"]="candidates_unverified"
                    row["image_candidates"]=candidates.get("count",0)
                    candidate_path=Path(summary["case_dir"])/"pilot-image-candidates.json"
                    candidate_path.write_text(json.dumps(candidates,ensure_ascii=False,indent=2)+"\n")
                    os.chmod(candidate_path,0o600)
                except (OSError,ValueError,KeyError,TypeError) as exc:
                    row["image_status"]="research_unavailable"
                    row["image_error"]=type(exc).__name__
        except (OSError,ValueError,KeyError,TypeError) as exc:
            row["status"]="pilot_error"
            row["error_type"]=type(exc).__name__
        manifest["channels"].append(row)
    manifest["generated"]=sum(int(row["article_generated"]) for row in manifest["channels"])
    manifest["text_checks_complete"]=sum(int(row["text_checks_complete"]) for row in manifest["channels"])
    # Image discovery isn't licensing/selection/visual QA; no fabricated green flags.
    manifest["fully_ready"]=0
    manifest["status"]="pipeline_attempted_no_fully_approved_illustrations"
    return manifest


def main():
    path=Path(os.environ["RUNNER_TEMP"])
    root=path/"zen-three-private"
    start=monotonic()
    report={"schema":"zen-three-channel-real-pilot-v1","requested":3,
            "publication_authorized":False,"rss_authorized":False}
    try:
        report=run(root)
    except Exception as exc:
        report.update(status="pilot_blocked",error_type=type(exc).__name__,
                      generated=0,fully_ready=0,channels=[])
    report["elapsed_seconds"]=round(monotonic()-start,2)
    rows=report.get("channels",[])
    key=os.environ.get("ZEN_REVIEW_PUBLIC_KEY_HEX","")
    if key and rows:
        try:
            receipt=save_encrypted_review(root,rows,path/"zen-three-private-review.enc",key)
            report["encrypted_owner_review"] = receipt
        except (OSError,ValueError,KeyError,TypeError) as exc:
            report["encrypted_owner_review"]={"status":"error","reason":type(exc).__name__}
    else:
        report["encrypted_owner_review"]={"status":"not_created"}
    # Never place topics, prose, source documents, case paths or full model logs in public CI artifacts.
    for row in rows:
        row.pop("case_dir",None)
        row.pop("pending",None)
    output=path/"zen-three-pilot-metadata.json"
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    os.chmod(output,0o600)
    print(json.dumps({"requested":3,"generated":report.get("generated",0),
                      "text_checks_complete":report.get("text_checks_complete",0),
                      "fully_ready":report.get("fully_ready",0),
                      "status":report.get("status","attempted"),
                      "error_type":report.get("error_type"),
                      "elapsed_seconds":report["elapsed_seconds"]},ensure_ascii=False))
    # A successful process is not a successful batch. Three source-checked, image-verified
    # ready articles are required to return green; candidate photos never count.
    return 0 if report.get("fully_ready")==3 else 2


if __name__=="__main__":
    raise SystemExit(main())
