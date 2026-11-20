#!/usr/bin/env python3
"""Real-model two-genre author proof; never emit generated article text.

A successful metadata report proves only LLM writer adapter/contract, not the
full nine-article pipeline or owner-ready publication.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import monotonic

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_writer import command_provider, write_article, Pending
from editorial_agent_v1 import article_digest
from editorial_length import count_characters, length_finding

CASES=[
    ("legends","Ночной звонок из квартиры, которую запечатали двадцать лет назад"),
    ("cinema","Кинообзор оригинального фильма Помни (2000) Кристофера Нолана"),
]


def run():
    provider=command_provider(ROOT/"src"/"editorial_local_llm.py")
    out={"schema":"zen-real-qwen-writing-proof-v1",
         "date":datetime.now(timezone.utc).isoformat(),
         "backend":os.environ.get("ZEN_LLM_BACKEND"),
         "model":os.environ.get("ZEN_LLM_MODEL"),
         "model_revision":os.environ.get("ZEN_LLM_MODEL_REVISION"),
         "real_generation_attempted":True,
         "public_draft_text_emitted":False,
         "publication_authorized":False,
         "rss_authorized":False,
         "full_editorial_checks_completed":False,
         "entries":[]}
    for channel,topic in CASES:
        started=monotonic()
        result={"channel":channel,"status":"error","real_model_response_verified":False}
        try:
            article=write_article(channel,topic,provider,allow_length_repair=True)
            n=count_characters(article["body_html"])
            result.update(status="generated" if length_finding(n) is None
                          else "generated_needs_length_repair",
                          real_model_response_verified=True,
                          text_sha256=article_digest(article),
                          characters_without_whitespace=n,
                          paragraph_count=article["body_html"].count("<p>"),
                          release_hold=article["release_hold"])
        except (Pending,ValueError,TypeError,KeyError,OSError) as exc:
            result["status"]="failed"
            result["error_type"]=type(exc).__name__
            result["reason"]=str(exc)[:100] if isinstance(exc,ValueError) else "provider_or_io_failure"
        result["runtime_seconds"]=round(monotonic()-started,2)
        out["entries"].append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    path=Path(os.environ["RUNNER_TEMP"])/"real-qwen-writer-metadata.json"
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return 0 if all(x["real_model_response_verified"] for x in out["entries"]) else 2


if __name__=="__main__":
    raise SystemExit(run())
