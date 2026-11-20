#!/usr/bin/env python3
"""Read-only multi-article validation of existing pinned ruBERT v4.

Only the repo's ruBERT is used. No site writes, no RSS, no new AI services.
Real article provenance is unknown; this validates index calculation, not FPR.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from editorial_ai_check import extract_text
from rubert_calibration import document_coverage_score, legacy_window_spans

SLUGS=[
    "legends-01-prohodnaya-17",
    "legends-02-poslednyaya-elektrichka",
    "legends-03-lift-dvoynoy-zvonok",
    "science-01-krasnye-spraity",
    "science-02-svetyascheesya-more",
    "science-03-tardigrady",
]
OUTPUT=ROOT/"editorial-calibration-reports"/"cross-article.json"

def run():
    rows=[]
    with tempfile.TemporaryDirectory(prefix="rubert-calibration-") as temp:
        p=Path(temp)/"input.txt"
        for slug in SLUGS:
            article=json.loads((ROOT/"content/articles"/(slug+".json")).read_text("utf-8"))
            assert article["slug"]==slug
            assert article["channel"] in {"legends","science"}
            content=extract_text(article["body_html"])
            words=re.findall(r"\S+",content)
            assert len(words)>=85
            p.write_text(content,encoding="utf-8")
            cmd=[str(ROOT/".rubert-venv/bin/python"),
                 str(ROOT/"src/rubert_v4_runner.py"),
                 "--source",str(ROOT/".rubert-upstream"),str(p)]
            completed=subprocess.run(cmd,capture_output=True,text=True,timeout=600,check=True)
            result=json.loads(completed.stdout)
            full=result["whole_document"]
            assert full["all_words_covered"]
            assert full["words_evaluated"]==len(words)
            assert full["windows_evaluated"]==result["window_count"]
            assert result["window_count"]==len(result["window_probabilities"])
            assert result["window_spans"]==legacy_window_spans(len(words))
            calculated=document_coverage_score(
                result["window_probabilities"],result["window_spans"],len(words))
            assert abs(calculated["score_percent"]-full["score_percent"])<=.01
            row={
                "slug":slug,"channel":article["channel"],
                "words":len(words),"window_count":result["window_count"],
                "max_window_index_100":round(result["score_percent"],2),
                "whole_document_index_100":full["score_percent"],
                "p75_window_index_100":round(sorted(result["window_probabilities"])[
                  round((len(result["window_probabilities"])-1)*.75)]*100,2),
                "native_prediction":result["prediction_native"],
                "article_ai_authorship_label":None,
                "is_ground_truth_for_calibration":False,
                "all_words_scored":True,
            }
            print("RUBERT_REAL_ARTICLE",json.dumps(row,ensure_ascii=False),flush=True)
            rows.append(row)
    report={
        "schema":"rubert-cross-article-2026-11-20-v1",
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "model":"existing_ruBERT_v4",
        "scope":"3 existing legends + 3 existing science articles, shadow only",
        "articles":rows,
        "all_articles_processed":len(rows)==len(SLUGS),
        "calibrated_ai_probability":False,
        "false_positive_rate_measurable":False,
        "release_gate_changed":False,
        "publication_authorized":False,
        "site_rss_modified":False,
        "note":"Unknown article authorship; compare metric consistency only. High scores do not prove AI authorship."
    }
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("BATCH_SUMMARY",json.dumps({"articles":len(rows),"success":True,
          "publication_authorized":False,"release_gate_changed":False},ensure_ascii=False))

if __name__=="__main__":
    run()
