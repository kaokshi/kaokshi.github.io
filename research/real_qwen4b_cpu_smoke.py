#!/usr/bin/env python3
"""Genuine CPU Qwen4B author smoke on GitHub: no text in logs or artifacts.

Neither a complete production launch nor fact/rights approval.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_writer import command_provider, write_article, Pending
from editorial_length import count_characters, length_finding
from editorial_agent_v1 import article_digest

TOPICS={
    "legends": "В квартире на последнем этаже каждую ночь звонит телефон, которого не существует",
    "science": "Почему красные спрайты возникают над мощными грозами",
}

def main():
    result={"schema":"zen-real-qwen4b-cpu-smoke-v1","created_at":datetime.now(timezone.utc).isoformat(),
            "model":"Qwen3-4B-Instruct-2507 GGUF Q4_K_M",
            "executor":"real_llama_cpp_cpu",
            "articles":[],"publication_authorized":False,"rss_authorized":False,
            "text_uploaded_to_public_artifacts":False,
            "disclaimer":"This validates local generation ONLY; no factcheck, illustration rights or Model A performed."}
    provider=command_provider(ROOT/"src"/"editorial_local_llm.py")
    for channel,topic in TOPICS.items():
        row={"channel":channel,"status":"not_started","generated_by_real_model":False}
        try:
            article=write_article(channel,topic,provider,allow_length_repair=True)
            count=count_characters(article["body_html"])
            digest=article_digest(article)
            row.update(status="draft_generated" if length_finding(count) is None
                       else "draft_generated_needs_length_repair",
                       generated_by_real_model=True,
                       visible_nonwhitespace_chars=count,sha256=digest,
                       paragraph_count=article["body_html"].count("<p>"),
                       requires_factcheck=True,
                       requires_image_rights_review=True,
                       requires_editorial_checks=True)
        except Pending as exc:
            row.update(status=exc.status,error_type="Pending")
        except (ValueError,TypeError,KeyError,OSError) as exc:
            row.update(status="writer_validation_failed",error_type=type(exc).__name__)
        result["articles"].append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    path=Path(os.environ["RUNNER_TEMP"])/"qwen4b-real-smoke-metadata.json"
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("metadata_report",path)
    return 0 if len(result["articles"])==2 and all(row["generated_by_real_model"] for row in result["articles"]) else 2

if __name__=="__main__":
    raise SystemExit(main())
