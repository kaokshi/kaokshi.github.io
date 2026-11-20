#!/usr/bin/env python3
"""Length-matched audit using ONLY newsroom pinned ruBERT and public test texts.

No new detector. No corpus texts stored. This tests domain-length confounding,
not calibrated probabilities or production-ready thresholds.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

import rubert_labeled_benchmark as base

LENGTHS={"essay":300,"scientific":500}
N_PER_CLASS=30
REPORT=base.ROOT/"editorial-calibration-reports/length-matched-controls.json"

def take_deterministic_excerpt(text,length,identity):
    words=text.split()
    if len(words)<length:
        raise ValueError("Not enough words")
    span=len(words)-length+1
    offset=int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8],"big")%span
    return " ".join(words[offset:offset+length])

def run():
    original_min=base.MIN_WORDS
    original_samples=base.SAMPLES_PER_CLASS
    chosen=[]
    metadata={}
    try:
        base.SAMPLES_PER_CLASS=N_PER_CLASS
        for genre,length in LENGTHS.items():
            base.MIN_WORDS=length
            rows,selection=base.select_rows(genre)
            for row in rows:
                row["genre"]=genre
                row["original_word_count"]=row["word_count"]
                row["text"]=take_deterministic_excerpt(row["text"],length,
                         genre+":"+row["label"]+":"+row["source_id"])
                row["word_count"]=length
            counts=Counter(row["label"] for row in rows)
            if min(counts.get("human",0),counts.get("ai",0))<N_PER_CLASS:
                raise ValueError("Not enough samples with matched length: "+genre+" "+str(counts))
            chosen.extend(rows)
            metadata[genre]=selection
    finally:
        base.MIN_WORDS=original_min
        base.SAMPLES_PER_CLASS=original_samples
    scored=base.score_rows(chosen)
    summary={}
    for genre,length in LENGTHS.items():
        rs=[r for r in scored if r["genre"]==genre]
        assert all(row["word_count"]==length for row in rs)
        by_label={}
        for label in ("human","ai"):
            ls=[x for x in rs if x["label"]==label]
            by_label[label]={"count":len(ls),
                "median_index":round(statistics.median(x["whole_document_index_100"] for x in ls),2),
                "native_ai_calls":sum(x["native_prediction"]=="ai" for x in ls)}
        summary[genre]={
            "fixed_word_count":length,
            "dataset_selection":metadata[genre],
            "by_label":by_label,
            "thresholds":[base.rates(rs,"whole_document_index_100",t)
                          for t in (25,35,45,55,65,75,85,90,95)],
            "false_positive_rate_confidence":"Wilson 95%, descriptive only",
        }
    output={
        "schema":"rubert-length-matched-public-controls-v1",
        "source_revision":base.SOURCE_COMMIT,
        "model":"unchanged_ruBERT_v4",
        "genres":summary,
        "total_tested":len(scored),
        "external_dataset_labels_independently_verified":False,
        "source_document_excerpts_equal_length_within_genre":True,
        "threshold_validated_for_release":False,
        "trained_or_tuned_model":False,
        "publication_authorized":False,
        "site_rss_modified":False,
        "caveat":"This is a resampled fixed-length diagnostic, not an independent holdout or proof of accuracy in Zen."
    }
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("LENGTH_MATCHED_SUMMARY",json.dumps({
        "total":len(scored),"summaries":{g:{"words":x["fixed_word_count"],"by_label":x["by_label"],
          "thresholds":x["thresholds"]} for g,x in summary.items()},
        "production_threshold_unchanged":True},ensure_ascii=False))

if __name__=="__main__":
    run()
