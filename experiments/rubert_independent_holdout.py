#!/usr/bin/env python3
"""Independent held-out confirmation of PRE-REGISTERED ruBERT candidate cutoffs.

No model tuning, no new AI checker, no publication. Source IDs used in the
30+30/class fixed-length pilot are excluded from this evaluation by genre.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

import rubert_labeled_benchmark as base
from rubert_length_control import LENGTHS, N_PER_CLASS as DEV_CLASS_N, take_deterministic_excerpt

HELDOUT_N_PER_CLASS=60
CANDIDATE_LIMITS={"essay":95, "scientific":85}
OUTPUT=base.ROOT/"editorial-calibration-reports/independent-holdout.json"

def holdout_rows(genre,length):
    previous_min,previous_n=base.MIN_WORDS,base.SAMPLES_PER_CLASS
    try:
        base.MIN_WORDS=length
        base.SAMPLES_PER_CLASS=DEV_CLASS_N
        dev,dev_selection=base.select_rows(genre)
    finally:
        base.MIN_WORDS=previous_min
        base.SAMPLES_PER_CLASS=previous_n
    dev_ids={r["source_id"] for r in dev}
    selected=[]
    total_candidates={}
    for label,path in base.FILES[genre].items():
        available=[]
        for row in base.fetch_json(path):
            if not isinstance(row,dict) or row.get("source")!=label:
                continue
            text=row.get("text")
            identifier=row.get("id")
            if not isinstance(text,str) or identifier is None:
                continue
            group=str(identifier)
            words=text.split()
            if not length<=len(words)<=base.MAX_WORDS or group in dev_ids:
                continue
            available.append((group,text))
        total_candidates[label]=len(available)
        ordered=sorted(available,key=lambda t:hashlib.sha256(
            (genre+":"+label+":"+t[0]).encode()).hexdigest())
        # Deduplicate source id within each label
        seen=set()
        for source_id,text in ordered:
            if source_id in seen:
                continue
            seen.add(source_id)
            subset=take_deterministic_excerpt(text,length,genre+":"+label+":"+source_id)
            selected.append({"label":label,"genre":genre,
              "source_id":source_id,"word_count":length,"text":subset})
            if len(seen)>=HELDOUT_N_PER_CLASS:
                break
    counts=Counter(row["label"] for row in selected)
    if min(counts.get("human",0),counts.get("ai",0))<HELDOUT_N_PER_CLASS:
        raise ValueError("Not enough non-overlapping holdout samples in "+genre+
                         ": "+str(counts)+", pool="+str(total_candidates))
    assert all(r["source_id"] not in dev_ids for r in selected)
    return selected, {"excluded_development_source_ids":len(dev_ids),
                      "development_selection":dev_selection["selection"],
                      "holdout_pool_after_exclusions":total_candidates,
                      "per_label":dict(counts),
                      "source_group_separated":True}

def run():
    selected=[]
    metadata={}
    for genre,length in LENGTHS.items():
        rows,info=holdout_rows(genre,length)
        selected.extend(rows)
        metadata[genre]=info
    scored=base.score_rows(selected)
    result={}
    for genre,length in LENGTHS.items():
        subset=[x for x in scored if x["genre"]==genre]
        limit=CANDIDATE_LIMITS[genre]
        bylabel={}
        for label in ("human","ai"):
            rows=[x for x in subset if x["label"]==label]
            bylabel[label]={
                "count":len(rows),
                "median_article_index":round(statistics.median(x["whole_document_index_100"] for x in rows),2),
                "native_ai_predictions":sum(x["native_prediction"]=="ai" for x in rows)}
        performance=base.rates(subset,"whole_document_index_100",limit)
        result[genre]={
            "pre_registered_threshold":limit,
            "fixed_words":length,
            "group_separation":metadata[genre],
            "performance":performance,
            "descriptive_medians":bylabel,
            "fpr_target_5pct_observed":performance["human_false_flag_rate"]<=0.05,
            "fpr_target_5pct_95pct_upper_bound_met":
                performance["human_false_flag_95pct_wilson"][1]<=0.05,
            "candidate_acceptable_for_production":False,
            "reason":"Domain proxy and corpus origin/holdout calibration may not generalize to Zen.",
        }
    output={
        "schema":"rubert-independent-length-matched-holdout-v1",
        "source_revision":base.SOURCE_COMMIT,
        "model":"existing_ruBERT_v4",
        "thresholds_selected_before_holdout":CANDIDATE_LIMITS,
        "holdout_original_ids_excluded_from_pilot":True,
        "total_n":len(scored),
        "genres":result,
        "external_dataset_labels_independently_verified":False,
        "publishing_threshold_changed":False,
        "publication_authorized":False,
        "site_rss_modified":False,
        "warning":"FPR/TPR on dataset-label proxies, not a calibrated P(AI) and not a forecast of Zen moderation."
    }
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("INDEPENDENT_RUBERT_HOLDOUT_RESULTS",json.dumps({
        "total":len(scored),"genres":{k:{"threshold":v["pre_registered_threshold"],
        "performance":v["performance"],"fpr_upper_bound_met":v["fpr_target_5pct_95pct_upper_bound_met"]}
        for k,v in result.items()},
        "publication_authorized":False},ensure_ascii=False),flush=True)

if __name__=="__main__":
    run()
