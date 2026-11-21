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


def create_plan(day, workspace, provider, *, per_channel=3):
    """Ask for three topics per channel, salvaging valid rows across retries.

    Do not make a weak local model generate nine simultaneous constrained rows.
    Never fill a missing item with a fabricated topic or bypass duplicates.
    """
    date.fromisoformat(day)
    if not (day.startswith("20") and len(day) == 10):
        raise ValueError("Expected a calendar date YYYY-MM-DD")
    if type(per_channel) is not int or per_channel not in (1, 3):
        raise ValueError("per_channel must be 1 for pilot or 3 for daily production")
    archive = existing_topics(workspace)
    selected = []
    instructions = {
        "legends": (
            "Предложи до ТРЁХ новых самобытных тем для канала городских "
            "мистических историй. Не повторяй готовые истории, выбирай "
            "конкретные необычные ситуации, а не общие жанры."
        ),
        "science": (
            "Предложи до ТРЁХ новых проверяемых научно-популярных тем. "
            "Для каждой укажи отдельный английский research_query для "
            "поиска первичных исследований. Не выдумывай подтверждения."
        ),
        "cinema": (
            "Предложи до ТРЁХ реально существующих фильмов для обзоров "
            "или пересказов. Записывай каждую тему только как "
            "«Название фильма» (год) — обзор без спойлеров или "
            "«Название фильма» (год) — пересказ со спойлерами. "
            "Не выдумывай фильмы и годы: их проверят отдельно."
        ),
    }
    for channel in ("legends", "science", "cinema"):
        accepted = []
        for attempt in range(4):
            missing = per_channel - len(accepted)
            if not missing:
                break
            try:
                reply = provider("plan_channel_topics", {
                    "date": day, "channel": channel, "needed": missing,
                    "existing_titles": (archive + [x["topic"] for x in selected + accepted])[-120:],
                    "instruction": (
                        instructions[channel] +
                        f" Верни ровно {missing} вариантов в JSON tasks с полями "
                        "topic, research_query. Без канала в каждой записи. "
                        "Для legends/cinema research_query — пустая строка. "
                        "В предложенных темах нельзя повторять предыдущие. "
                        "Вся статья будет создана агентом самостоятельно."
                    ),
                })
            except Pending:
                raise
            if not isinstance(reply, dict) or not isinstance(reply.get("tasks"), list):
                continue
            for item in reply["tasks"][:max(per_channel, missing)]:
                if len(accepted) >= per_channel:
                    break
                if not isinstance(item, dict):
                    continue
                topic, query = item.get("topic"), item.get("research_query")
                if not isinstance(topic, str) or not 8 <= len(topic.strip()) <= 180:
                    continue
                topic = topic.strip()
                if not isinstance(query, str):
                    continue
                if channel == "science" and not query.strip():
                    continue
                if channel != "science" and query.strip():
                    continue
                if channel == "cinema":
                    import re
                    if not re.search(r"«[^»]{2,100}»\s*\((?:19|20)\d{2}\)", topic):
                        continue
                old = archive + [x["topic"] for x in selected + accepted]
                if topic_key(topic) in {topic_key(x) for x in old}:
                    continue
                if topic_duplicate_reason(channel, topic, old):
                    continue
                record = {"channel": channel, "topic": topic}
                if channel == "science":
                    record["research_query"] = query.strip()
                accepted.append(record)
        if len(accepted) != per_channel:
            raise ValueError(f"planner_channel_incomplete:{channel}:{len(accepted)}/{per_channel}")
        selected.extend(accepted)
    plan = {"date": day, "tasks": selected,
            "generation_provenance": "local_llm_per_channel_topic_proposal",
            "per_channel": per_channel,
            "article_generation_completed": False, "sources_verified": False,
            "film_facts_verified": False, "publication_authorized": False}
    if per_channel == 3:
        validate_plan(plan)
    else:
        # The three-article smoke test must not require an unrelated nine-item
        # production plan; all topic-level guards above remain mandatory.
        assert len(plan["tasks"]) == 3
        assert {x["channel"] for x in plan["tasks"]} == {"legends", "science", "cinema"}
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
