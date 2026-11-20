#!/usr/bin/env python3
"""Generate an evidence-labeled nine-topic DAILY PLAN via a real local LLM.

All topic proposals remain private, draft-only and subject to editorial checks.
Never invents article/source completion or publishes anything.
"""
import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path

from editorial_ai_check import CONTENT
from editorial_nine_draft_queue import validate_plan
from editorial_writer import Pending, command_provider, private_path, topic_key
from editorial_no_repeat import topic_duplicate_reason

PROVIDER=Path(__file__).resolve().parent/"editorial_local_llm.py"


def existing_topics(workspace):
    titles=[]
    for path in CONTENT.glob("*.json"):
        a=json.loads(path.read_text(encoding="utf-8"))
        if isinstance(a.get("title"),str):
            titles.append(a["title"])
    for path in workspace.glob("nine-topics-*.json"):
        try:
            prior=json.loads(path.read_text(encoding="utf-8"))
            titles.extend(row["topic"] for row in prior.get("tasks",[]) if isinstance(row,dict)
                          and isinstance(row.get("topic"),str))
        except (ValueError,KeyError,TypeError,OSError):
            raise ValueError("Unable to validate previous plan; do not risk repeated topics")
    return titles


def create_plan(day,workspace,provider):
    date.fromisoformat(day)
    if not (day.startswith("20") and len(day)==10):
        raise ValueError("Expected a calendar date YYYY-MM-DD")
    archive=existing_topics(workspace)
    reply=provider("plan_topics",{
        "date":day,"existing_titles":archive[-120:],
        "instruction":(
            "Подготовь РОВНО 9 РАЗНЫХ тем для русскоязычных каналов Яндекс Дзена: "
            "3 legends (самобытная городская мистика), 3 science (необычные, но проверяемые явления), "
            "3 cinema (конкретные существующие фильмы, выразительные обзоры или точные пересказы). "
            "Не повторяй существующие заголовки или темы. Не предлагай выдуманных фильмов, "
            "не обещай проверенных источников, не выдумывай фактов о кинопроизводстве. "
            "Для каждой научной темы дай research_query на английском для поиска независимых "
            "первоисточников. Для мистики и кино research_query возвращай пустую строку. "
            "Строго JSON {tasks:[{channel,topic,research_query}]}, ровно три каждой категории."
        )
    })
    if not isinstance(reply,dict):
        raise ValueError("Planner returned invalid JSON")
    tasks=[]
    for x in reply.get("tasks",[]):
        if not isinstance(x,dict):
            raise ValueError("Malformed topic object")
        cleaned={"channel":x.get("channel"),"topic":x.get("topic")}
        if x.get("channel")=="science":
            cleaned["research_query"]=x.get("research_query")
        tasks.append(cleaned)
    plan={"date":day,"tasks":tasks,"generation_provenance":"local_llm_topic_proposal",
          "article_generation_completed":False,
          "sources_verified":False,"film_facts_verified":False,
          "publication_authorized":False}
    validate_plan(plan)
    prior={topic_key(t) for t in archive}
    if any(topic_key(x["topic"]) in prior for x in tasks):
        raise ValueError("Topic exactly repeats a previous draft or plan")
    for item in tasks:
        issue = topic_duplicate_reason(item["channel"], item["topic"], archive)
        if issue:
            raise ValueError("Refuse repeated editorial topic: " + issue)
    return plan


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--date",default=date.today().isoformat())
    p.add_argument("--workspace",type=Path,required=True)
    p.add_argument("--provider",type=Path,default=PROVIDER)
    args=p.parse_args()
    workspace=private_path(args.workspace)
    workspace.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=workspace/("nine-topics-"+args.date+".json")
    if path.exists():
        p.error("Existing topic plan may not be overwritten. Reuse it to resume the queue.")
    try:
        plan=create_plan(args.date,workspace,command_provider(args.provider))
    except (Pending,ValueError,KeyError,TypeError,OSError) as exc:
        p.error(f"Topic plan not generated: {type(exc).__name__}: {str(exc)[:130]}")
    path.write_text(json.dumps(plan,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    os.chmod(path,0o600)
    print(json.dumps({"plan":str(path),"tasks":len(plan["tasks"]),
                      "status":"proposed_not_fact_checked","publication_authorized":False},
                     ensure_ascii=False))


if __name__=="__main__":
    main()
