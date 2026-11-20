#!/usr/bin/env python3
"""Real nine-article production-path rehearsal using a local Qwen executor.

This is NOT a nine-article publication, and cannot claim editorial approval.
All full text, sources and model responses stay in RUNNER_TEMP, NEVER uploaded.
Metadata on exact stages/versions, without drafts, is the only public artifact.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import monotonic

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_nine_draft_queue import execute, validate_plan, CHANNELS
from editorial_writer import private_path

# Existing films, genre-specific mysteries, scientific topics searchable in Europe PMC.
TASKS=[
 {"channel":"legends","topic":"Звонок из квартиры, которую опечатали двадцать лет назад"},
 {"channel":"legends","topic":"Последняя электричка, на которой никто не выходит"},
 {"channel":"legends","topic":"Ночной консьерж и отражение в пустом лифте"},
 {"channel":"science","topic":"Как тихоходки переживают полное высыхание","research_query":"tardigrade desiccation tolerance protective proteins"},
 {"channel":"science","topic":"Почему человеку необходимы циркадные ритмы","research_query":"human circadian rhythm clock sleep clinical physiology"},
 {"channel":"science","topic":"Откуда берётся биолюминесценция морских бактерий","research_query":"marine bacterial bioluminescence lux operon"},
 {"channel":"cinema","topic":"Полный пересказ со спойлерами фильма Начало 2010 года"},
 {"channel":"cinema","topic":"Обзор со спойлерами фильма Престиж 2006 года"},
 {"channel":"cinema","topic":"Пересказ сюжета со спойлерами фильма Интерстеллар 2014 года"},
]
PLAN={"date":"2026-11-21","tasks":TASKS}
# Existing pipeline enforces 2 agent + 1 assistant maximum and no release.
def per_task_status(item):
    execution=item.get("execution",{})
    return {
      "task_id":item.get("task_id"),"channel":item.get("channel"),
      "status":item.get("status"),"versions":item.get("versions",0),
      "generated":item.get("draft_sha256") is not None,
      "draft_sha256":item.get("draft_sha256"),
      "draft_chars_without_whitespace":item.get("draft_chars_without_whitespace"),
      "length_ok":item.get("length_ok",False),
      "automated_checks_completed":item.get("last_checks_completed",False),
      "last_checked_sha256":item.get("last_checked_sha256"),
      "manual_checks_pending":item.get("manual_checks_pending",[]),
      "article_ready_for_owner":item.get("article_ready_for_owner",False),
      "pipeline_status":execution.get("pipeline_status",execution.get("status")),
      "exit_code":execution.get("exit_code"),
      "execution_error_type":execution.get("error",execution.get("reason")),
      "publication_authorized":False
    }

def main():
    validate_plan(PLAN)
    root=private_path(Path(os.environ["RUNNER_TEMP"])/"zen-real-nine-private")
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    start=monotonic()
    meta={"schema":"zen-real-nine-qwen-full-path-rehearsal-v1",
          "timestamp":datetime.now(timezone.utc).isoformat(),
          "model":os.environ.get("ZEN_LLM_MODEL"),
          "model_revision":os.environ.get("ZEN_LLM_MODEL_REVISION"),
          "source_research":"europe_pmc_only_for_science",
          "scope":"actual_editorial_pipeline_on_9_distinct_topics",
          "requested":9,"channels_expected":{c:3 for c in CHANNELS},
          "article_text_exported":False,"publication_authorized":False,
          "rss_authorized":False,"transport_status":"not_connected",
          "limitations":["no owner's content approval",
                         "cinema plot facts need independent source corroboration",
                         "image rights and 7-8 image selection not automatically completed",
                         "full editorial and style checks succeed only if actual toolchain returns completed"]}
    try:
        results=execute(PLAN,root)
        rows=[per_task_status(item) for item in results["tasks"]]
        meta.update(model_health=results["model_health"],
                    rows=rows,
                    generated=sum(r["generated"] for r in rows),
                    valid_length=sum(r["length_ok"] for r in rows),
                    complete_automated_checks=sum(r["automated_checks_completed"] for r in rows),
                    ready_for_owner=sum(r["article_ready_for_owner"] for r in rows),
                    all_nine_pipeline_exit_ok=all(r["exit_code"]==0 for r in rows))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        meta.update(status="framework_error",error_type=type(exc).__name__,
                    rows=[],generated=0,valid_length=0,complete_automated_checks=0)
    meta["duration_seconds"]=round(monotonic()-start,2)
    path=Path(os.environ["RUNNER_TEMP"])/"zen-real-nine-public-metadata.json"
    path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    os.chmod(path,0o600)
    print(json.dumps({k:meta.get(k) for k in
      ("requested","generated","valid_length","complete_automated_checks","ready_for_owner",
       "duration_seconds","all_nine_pipeline_exit_ok","status","error_type")},
       ensure_ascii=False),flush=True)
    for item in meta.get("rows",[]):
        print(json.dumps({k:item.get(k) for k in
          ("channel","status","versions","generated","length_ok",
           "automated_checks_completed","article_ready_for_owner","exit_code")},
           ensure_ascii=False),flush=True)
    if not meta.get("all_nine_pipeline_exit_ok"):
        return 2
    # Exit zero does not claim full success without the 9 SHA-bound checks.
    return 0 if meta.get("complete_automated_checks")==9 else 2

if __name__=="__main__":
    raise SystemExit(main())
