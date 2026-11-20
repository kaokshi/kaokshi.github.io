"""Retest one real Qwen legends draft using the recovered writer contract.

Prints metadata only: never generated paragraphs, title, or CTA.
"""
from pathlib import Path
from time import monotonic
import json
import os
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from editorial_writer import write_article, command_provider, WriterOutputError, Pending
from editorial_length import count_characters, length_finding
from editorial_agent_v1 import article_digest

def main():
    provider=command_provider(Path(__file__).resolve().parents[1]/"src"/"editorial_local_llm.py")
    attempts=[]
    def tracked(task,payload):
        attempts.append(task)
        return provider(task,payload)
    start=monotonic()
    result={"channel":"legends","real_model_used":True,"generation_success":False,
            "publication_authorized":False,"rss_authorized":False}
    try:
        article=write_article("legends",
            "Ночной звонок из квартиры, которую запечатали двадцать лет назад",
            tracked,allow_length_repair=True)
        chars=count_characters(article["body_html"])
        result.update(generation_success=True,
            characters_without_whitespace=chars,
            length_ok=length_finding(chars) is None,
            text_sha256=article_digest(article),
            paragraph_count=article["body_html"].count("<p>"))
    except (Pending,WriterOutputError,ValueError,KeyError,TypeError,OSError) as exc:
        result["failure_type"]=type(exc).__name__
        result["failure_code"]=exc.code if isinstance(exc,WriterOutputError) else (
            "provider_problem" if isinstance(exc,Pending) else "validation_or_io_failure")
    result["attempts"]=len(attempts)
    result["duration_seconds"]=round(monotonic()-start,2)
    output=Path(os.environ["RUNNER_TEMP"])/"qwen-legends-recovery-metadata.json"
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result["generation_success"] and result["length_ok"] else 2

if __name__=="__main__":
    raise SystemExit(main())
