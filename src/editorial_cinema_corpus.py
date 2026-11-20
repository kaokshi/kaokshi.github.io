#!/usr/bin/env python3
"""Reproducible CINEMA-domain corpus audit, no model training/release.

Human movie review vs LLM review datasets must not be confused with long
narrative film recaps. Outputs metadata and hashes, never copyrighted text.
"""
import hashlib
import json
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
from datasets import load_dataset

OUT=Path("editorial-cinema-corpus-audit")
OUT.mkdir(exist_ok=True)

def russian_fraction(value):
    letters=[c for c in value if c.isalpha()]
    return (sum('а'<=c.lower()<='я' or c.lower()=='ё' for c in letters)/len(letters)) if letters else 0

def valid_pair(human, machine, min_len=350):
    return (isinstance(human,str) and isinstance(machine,str)
            and len(human)>=min_len and len(machine)>=min_len
            and russian_fraction(human)>.75 and russian_fraction(machine)>.75
            and human.strip()!=machine.strip())

def count_movie_demo():
    ds=load_dataset("seckur-tech/RuTextLLMDetect")
    if not hasattr(ds,"keys"):
        raise ValueError("Expected splits in movie text provenance dataset")
    splits=list(ds.keys())
    hits=[]
    for split in splits:
        # The dataset includes five distinct genre splits; only films qualify.
        if "kino" not in split.lower() and "film" not in split.lower() and "movie" not in split.lower():
            continue
        for i,row in enumerate(ds[split]):
            human=row.get("summary")
            machine=row.get("ai_text")
            if valid_pair(human,machine):
                hits.append({"split":split,"row":i,"prompt_title_sha":hashlib.sha256(str(row.get("title","")).encode()).hexdigest(),
                             "human_sha256":hashlib.sha256(human.encode()).hexdigest(),
                             "ai_sha256":hashlib.sha256(machine.encode()).hexdigest(),
                             "human_chars":len(human),"ai_chars":len(machine),
                             "ai_model":row.get("model") or "publisher_claim_gpt4o"})
    return {"split_names":splits,"sampled_paired_rows":len(hits),"genre":"movie_reviews_not_long_recaps",
            "provenance":"dataset_publisher_claim_not_independently_verified",
            "rows":hits}

def count_kinopoisk_summaries():
    ds=load_dataset("Auttar/KinopoiskReviewsSummarization")
    stat={"dataset_splits":{},"relevant_pairs":[]}
    for split in ds.keys():
        examples=ds[split]
        possible=0
        for i,row in enumerate(examples):
            review=row.get("review")
            summary=row.get("summary")
            if valid_pair(review,summary):
                possible+=1
                if len(stat["relevant_pairs"])<100:
                    stat["relevant_pairs"].append({
                        "split":split,"row":i,
                        "human_sha256":hashlib.sha256(review.encode()).hexdigest(),
                        "ai_summary_sha256":hashlib.sha256(summary.encode()).hexdigest(),
                        "review_chars":len(review),"summary_chars":len(summary),
                        "provenance":"review_human_claim_summary_llm_distillation_claim"})
        stat["dataset_splits"][split]={"total":len(examples),"pairs_over_350_cyrillic_chars":possible}
    stat["warning"]="AI summaries are different-length tasks, not film retellings; not valid unbiased authorship calibration"
    return stat

def main():
    result={"schema":"movie-genre-corpus-audit-v1","date_utc":datetime.now(timezone.utc).isoformat(),
            "production_ready":False,"publication_authorized":False,"corpora":{},"errors":[]}
    try:result["corpora"]["seckur_demo"]=count_movie_demo()
    except Exception as e:result["errors"].append("Seckur demo: "+type(e).__name__+": "+str(e)[:350])
    try:result["corpora"]["kinopoisk_summary"]=count_kinopoisk_summaries()
    except Exception as e:result["errors"].append("Kinopoisk summary: "+type(e).__name__+": "+str(e)[:350])
    result["minimum_independent_human_holdout"]=120
    result["minimum_independent_ai_holdout"]=120
    result["status"]="research_genre_corpus_audited" if result["corpora"] and not result["errors"] else "incomplete_dataset_audit"
    (OUT/"cinema-corpus-manifest.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("CINEMA_CORPUS_AUDIT",json.dumps({"status":result["status"],
          "demo_pairs":result["corpora"].get("seckur_demo",{}).get("sampled_paired_rows"),
          "summary_counts":result["corpora"].get("kinopoisk_summary",{}).get("dataset_splits"),
          "errors":result["errors"],"production_ready":False},ensure_ascii=False),flush=True)
    if result["errors"]:
        raise SystemExit("Missing or unreadable genre data; cinema model remains untrained")

if __name__=="__main__":main()
