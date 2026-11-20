#!/usr/bin/env python3
"""External-label benchmark USING ONLY existing pinned GitHub ruBERT v4.

Fetch PUBLIC labeled corpus data as test inputs, not a detection service.
Never upload texts or model outputs containing texts, never publish any article.
Labels are the dataset authors' claims, not independently verified provenance.
"""
import collections
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from rubert_calibration import document_coverage_score, legacy_window_spans, summarize_windows
from rubert_v4_runner import WINDOW_WORDS, STRIDE_WORDS

SOURCE_REPO="CoffeBank/Ru-hard-detection-dataset"
SOURCE_COMMIT="2d6477427de88ec8ad86698917fe16f49a556b81"
BASE=f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_COMMIT}/main"
FILES={
    "essay": {"human": "essay/original_essay.json", "ai": "essay/generated_essays.json"},
    "scientific": {"human": "scientific_texts/orig_scientific.json",
                   "ai": "scientific_texts/generated_scientific.json"},
}
SAMPLES_PER_CLASS=20
MIN_WORDS=120
MAX_WORDS=1300
REPORT=ROOT/"editorial-calibration-reports/verified-label-benchmark.json"

def fetch_json(path):
    url=f"{BASE}/{path}"
    req=Request(url,headers={"User-Agent":"ZenEditorialCalibration/1.0"})
    with urlopen(req,timeout=75) as r:
        data=r.read(30_000_000)
    rows=json.loads(data)
    if not isinstance(rows,list):
        raise ValueError("Dataset is not a list: "+path)
    return rows

def select_rows(genre):
    loaded={}
    for label,path in FILES[genre].items():
        rows=fetch_json(path)
        good=[]
        for row in rows:
            if not isinstance(row,dict):
                continue
            actual=row.get("source")
            if actual!=label:
                continue
            text=row.get("text")
            ident=row.get("id")
            if not isinstance(text,str) or not text.strip() or ident is None:
                continue
            wc=len(text.split())
            if not MIN_WORDS<=wc<=MAX_WORDS:
                continue
            group=str(ident)
            good.append({"label":label,"text":text,"word_count":wc,
                         "source_id":group,
                         "group":f"{genre}:{group}"})
        loaded[label]=good
    # Only IDs common to both files are a trustworthy pairing key.
    overlap=set(r["source_id"] for r in loaded["human"]) & set(r["source_id"] for r in loaded["ai"])
    if len(overlap)>=SAMPLES_PER_CLASS:
        paired_h={r["source_id"]:r for r in loaded["human"]}
        paired_a={r["source_id"]:r for r in loaded["ai"]}
        selected_ids=sorted(overlap,key=lambda s:hashlib.sha256((genre+":"+s).encode()).hexdigest())[:SAMPLES_PER_CLASS]
        chosen=[r for i in selected_ids for r in (paired_h[i],paired_a[i])]
        selection="paired_by_source_id"
    else:
        # Not a paired corpus: source IDs from different classes are not taken
        # as same underlying article. Label and disclose this fallback.
        chosen=[]
        for label in ("human","ai"):
            xs=sorted(loaded[label],key=lambda r:hashlib.sha256(
                (genre+":"+label+":"+r["source_id"]).encode()).hexdigest())
            chosen.extend(xs[:SAMPLES_PER_CLASS])
        selection="independently_sampled_by_label_no_verified_pairing"
    counts=collections.Counter(r["label"] for r in chosen)
    if min(counts.get("human",0),counts.get("ai",0))<8:
        raise ValueError("Not enough texts to compare: "+str(counts))
    return chosen,{"selection":selection,
                   "available_count":{k:len(v) for k,v in loaded.items()},
                   "selected_count":dict(counts),
                   "sampling_seed":"SHA256(ids; fixed), not model scores"}

def wilson(k,n,z=1.96):
    if n==0:return None
    p=k/n
    den=1+z*z/n
    mid=(p+z*z/(2*n))/den
    rad=z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/den
    return [round(max(0,mid-rad),4),round(min(1,mid+rad),4)]

def rates(rows,score_key,threshold):
    h=[r for r in rows if r["label"]=="human"]
    a=[r for r in rows if r["label"]=="ai"]
    fp=sum(r[score_key]>threshold for r in h)
    tp=sum(r[score_key]>threshold for r in a)
    return {"threshold":threshold,"human_count":len(h),"ai_count":len(a),
            "human_false_flags":fp,
            "human_false_flag_rate":round(fp/len(h),4) if h else None,
            "human_false_flag_95pct_wilson":wilson(fp,len(h)),
            "ai_detected":tp,
            "ai_detection_rate":round(tp/len(a),4) if a else None,
            "ai_detection_95pct_wilson":wilson(tp,len(a))}

def score_rows(selected):
    sys.path.insert(0,str(ROOT/".rubert-upstream"))
    from aidetector.transformer_score import get, word_count
    scorer=get("v4")
    out=[]
    for idx,row in enumerate(selected):
        words=row["text"].split()
        spans=legacy_window_spans(len(words),WINDOW_WORDS,STRIDE_WORDS)
        segments=[" ".join(words[lo:hi]) for lo,hi in spans]
        values=[float(x) for x in scorer.proba(segments,bs=1)]
        global_index=document_coverage_score(values,spans,len(words))
        native=[float(scorer.threshold_for(word_count(s))) for s in segments]
        r={"label":row["label"],"genre":row.get("genre"),
           "word_count":len(words),"window_count":len(values),
           "max_index_100":round(max(values)*100,2),
           "whole_document_index_100":global_index["score_percent"],
           "native_prediction":"ai" if any(x>=t for x,t in zip(values,native)) else "human",
           "p75_index_100":round(summarize_windows(values)["p75"]*100,2)}
        out.append(r)
        print("LABELLED_BENCHMARK_PROGRESS",idx+1,"/",len(selected),row["genre"],row["label"],
              "words",len(words),"whole",r["whole_document_index_100"],flush=True)
    return out

def produce_report():
    selected=[]
    selection_info={}
    for genre in FILES:
        rows,metadata=select_rows(genre)
        for r in rows:r["genre"]=genre
        selected.extend(rows)
        selection_info[genre]=metadata
    scored=score_rows(selected)
    by_genre={}
    for genre in FILES:
        group=[r for r in scored if r["genre"]==genre]
        entries={}
        for label in ("human","ai"):
            ls=[r for r in group if r["label"]==label]
            entries[label]={"n":len(ls),
                "median_words":round(statistics.median(x["word_count"] for x in ls),1),
                "median_whole_index":round(statistics.median(x["whole_document_index_100"] for x in ls),2),
                "median_max_index":round(statistics.median(x["max_index_100"] for x in ls),2)}
        by_genre[genre]={"selection":selection_info[genre],
            "summaries":entries,
            "prespecified_thresholds":{
                metric:[rates(group,metric,threshold) for threshold in (20,25,35,45,55,65,75,85)]
                for metric in ("whole_document_index_100","max_index_100")
            }}
    report={
        "schema":"rubert-public-labeled-benchmark-v1",
        "model":"existing_pinned_ruBERT_v4",
        "dataset_repository":f"https://github.com/{SOURCE_REPO}/tree/{SOURCE_COMMIT}",
        "source_revision":SOURCE_COMMIT,
        "label_quality":"Declared by external dataset authors; not independently verified",
        "group_organization":"essay is a proxy for legends; scientific corpus is a proxy for science news",
        "evaluation_genres":by_genre,
        "total_n":len(scored),
        "confirmed_human_ai_ground_truth":False,
        "validated_for_zen_editorial_population":False,
        "change_production_threshold":False,
        "publication_authorized":False,
        "rss_modified":False,
        "method_warning":"Exploratory: labels/domain may be noisy or overlap training; small sample CIs are wide. Do not choose production threshold solely from this table.",
        "note":"All checking performed with original pinned ruBERT from our GitHub pipeline; dataset is test input only, not an external detection service.",
    }
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("LABELLED_BENCHMARK_SUMMARY",json.dumps({
        "count":len(scored),"labels":dict(collections.Counter(r["label"] for r in scored)),
        "summaries":{k:v["summaries"] for k,v in by_genre.items()},
        "release_gate_unchanged":True},ensure_ascii=False))

if __name__=="__main__":
    produce_report()
