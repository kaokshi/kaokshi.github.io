#!/usr/bin/env python3
"""Experimental four-existing-detector editorial benchmark (no release changes).

Human/AI labels are inherited from a public dataset, NOT independently verified.
ruBERT and AIW family are evaluated as two detector families; AI Accent is
descriptive style metadata, not a calibrated AI-probability. Output excludes texts.
"""
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
REPO="CoffeBank/Ru-hard-detection-dataset"
COMMIT="2d6477427de88ec8ad86698917fe16f49a556b81"
SOURCE=f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/main"
FILES={
 "essay":("essay/original_essay.json","essay/generated_essays.json",200),
 "scientific":("scientific_texts/orig_scientific.json","scientific_texts/generated_scientific.json",250),
}
PAIRS_PER_GENRE=20
RESULT=ROOT/"editorial-calibration-reports/four-models/benchmark.json"

def public_records(relative):
    url=f"{SOURCE}/{relative}"
    with urlopen(Request(url,headers={"User-Agent":"ZenGitHubEditorialBenchmark/1.0"}),timeout=100) as req:
        raw=req.read(28_000_000)
    rows=json.loads(raw.decode("utf-8"))
    if not isinstance(rows,list):
        raise ValueError("Unexpected source corpus")
    return rows

def select_pairs(genre):
    source_h,source_ai,words=FILES[genre]
    maps={}
    for label,source in (("human",source_h),("ai",source_ai)):
        result={}
        for row in public_records(source):
            if not isinstance(row,dict) or row.get("source")!=label:
                continue
            text=row.get("text")
            ident=row.get("id")
            if ident is None or not isinstance(text,str) or len(text.split())<words:
                continue
            # Prevent duplicates with ambiguous ground truth from being treated
            # as independent controls.
            ident=str(ident)
            if ident in result:
                continue
            result[ident]=text
        maps[label]=result
    available=set(maps["human"]) & set(maps["ai"])
    ids=sorted(available,key=lambda x:hashlib.sha256(
        (genre+"|"+x).encode("utf-8")).hexdigest())[:PAIRS_PER_GENRE]
    if len(ids)<PAIRS_PER_GENRE:
        raise ValueError(f"Not enough paired controls in {genre}: {len(ids)}")
    output=[]
    for i,ident in enumerate(ids):
        split="calibration" if i<PAIRS_PER_GENRE//2 else "holdout"
        for label in ("human","ai"):
            full=maps[label][ident].split()
            off=int.from_bytes(hashlib.sha256((genre+"|"+ident+"|"+label).encode()).digest()[:8],"big")%(len(full)-words+1)
            excerpt=" ".join(full[off:off+words])
            output.append({"genre":genre,"label":label,
             "source_group_sha256":hashlib.sha256((genre+"|"+ident).encode()).hexdigest(),
             "split":split,"word_count":words,"text":excerpt})
    return output

def call(argv,path,timeout=240):
    run=subprocess.run([*argv,str(path)],capture_output=True,text=True,timeout=timeout)
    if run.returncode:
        raise RuntimeError("First-party model failed: "+str(argv[0])+
                           ": "+(run.stderr or run.stdout)[-450:])
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError as ex:
        raise RuntimeError("Model did not return JSON: "+str(argv[0])) from ex

def finite01(v):
    if isinstance(v,bool) or not isinstance(v,(float,int)) or not 0<=v<=1:
        raise ValueError("Model returned invalid 0..1 score")
    return float(v)

def whole_index(vals,words,window=85,stride=65):
    """Match the current windowing; count every word once despite overlap."""
    if words<=window:
        spans=[(0,words)]
    else:
        starts=list(range(0,words-window+1,stride))
        if starts[-1]!=words-window:
            starts.append(words-window)
        spans=[(n,n+window) for n in starts]
    if len(vals)!=len(spans):
        raise ValueError("ruBERT windows/results mismatch")
    covered=[[] for _ in range(words)]
    for v,(start,end) in zip(vals,spans):
        v=finite01(v)
        for n in range(start,end):
            covered[n].append(v)
    if not all(covered):
        raise ValueError("ruBERT did not cover entire text")
    return round(100*sum(sum(x)/len(x) for x in covered)/words,3)

def extract_signals(text,path):
    rubert=call([str(ROOT/".rubert-venv/bin/python"),
      str(ROOT/"src/rubert_v4_runner.py"),"--source",str(ROOT/".rubert-upstream")],path,timeout=600)
    modern=call(["aiw-ru","classify","--model","modernbert","--json"],path)
    light=call(["aiw-ru","classify","--model","lightgbm","--json"],path)
    accent=call(["ai-accent-detector","--json","--lang","ru"],path)
    for model in (modern,light):
        if model.get("skipped") is True or model.get("read",{}).get("truncated") is True:
            raise ValueError("Model skipped or truncated a benchmark sample")
    ru_windows=[finite01(x) for x in rubert["window_probabilities"]]
    if len(text.split())!=rubert.get("word_count"):
        raise ValueError("Inconsistent word counts (full text not scored)")
    m=finite01(modern["probability"])
    l=finite01(light["probability"])
    accent_score=finite01(accent["ai_accent_score"])
    if accent.get("coverage",{}).get("level") not in ("high","medium"):
        raise ValueError("Insufficient accent detector coverage")
    ru_native=rubert["prediction_native"]=="ai"
    if rubert["prediction_native"] not in ("ai","human"):
        raise ValueError("Invalid ruBERT native classification")
    family_both=m>=.5 and l>=.5
    family_any=m>=.5 or l>=.5
    # These are explicitly PRE-REGISTERED RESEARCH RULES, not release decisions.
    return {
      "rubert_article_index_100":whole_index(ru_windows,len(text.split())),
      "rubert_max_100":round(100*max(ru_windows),2),
      "rubert_native_ai":ru_native,
      "modernbert_ai_signal":round(m,6),
      "lightgbm_ai_signal":round(l,6),
      "accent_style_index":round(accent_score,6),
      "aiw_family_concurrence":family_both,
      "two_family_strict":ru_native and family_both,
      "two_family_broad":ru_native and family_any,
      "accents_are_not_ai_votes":True
    }

def confusion(rows,predicate):
    h=[r for r in rows if r["label"]=="human"]
    a=[r for r in rows if r["label"]=="ai"]
    fp=sum(bool(r["models"][predicate]) for r in h)
    tp=sum(bool(r["models"][predicate]) for r in a)
    return {"human_n":len(h),"ai_n":len(a),"human_false_alarms":fp,
            "ai_detected":tp,"false_alarm_rate":round(fp/len(h),4) if h else None,
            "ai_detection_rate":round(tp/len(a),4) if a else None}

def main():
    samples=[]
    for genre in FILES:
        samples.extend(select_pairs(genre))
    with tempfile.TemporaryDirectory(prefix="zen-first-party-four-model-") as temp:
        path=Path(temp)/"input.txt"
        for i,row in enumerate(samples,1):
            path.write_text(row["text"],encoding="utf-8")
            result=extract_signals(row["text"],path)
            row["models"]=result
            del row["text"]
            print("FOUR_DETECTOR_PROGRESS",i,"/",len(samples),row["genre"],
                  row["split"],row["label"],
                  "ru",result["rubert_article_index_100"],
                  "modern",result["modernbert_ai_signal"],
                  "lightgbm",result["lightgbm_ai_signal"],
                  "accent",result["accent_style_index"],flush=True)
    summary={}
    for genre in FILES:
        summary[genre]={}
        for split in ("calibration","holdout"):
            group=[r for r in samples if r["genre"]==genre and r["split"]==split]
            summary[genre][split]={
                "paired_source_groups":len(set(r["source_group_sha256"] for r in group)),
                "by_rule":{name:confusion(group,name)
                    for name in ("two_family_strict","two_family_broad","aiw_family_concurrence","rubert_native_ai")},
                "model_scores_are_not_probabilities":True,
            }
    output={
        "schema":"four-existing-github-detectors-research-v1",
        "dataset":f"https://github.com/{REPO}/tree/{COMMIT}",
        "dataset_labels_self_declared_not_verified":True,
        "data_usage":"benchmark input only; no new checking service",
        "split":"Paired human/AI versions assigned together to calibration OR holdout",
        "all_4_existing_models_run":True,
        "models":{"rubert":"ruBERT v4","modernbert":"aiw-ru ModernBERT",
                  "lightgbm":"aiw-ru LightGBM","accent":"AI Accent Detector (style only)"},
        "same_training_warning":"ModernBERT and LightGBM share a training dataset; their agreement is not independent votes",
        "records":samples,
        "summary":summary,
        "genre_proxy_warning":"Essays != original urban legends; research articles != popular-science Zen posts",
        "no_training_overlap_audited":False,
        "threshold_validated":False,
        "auto_revision_enabled":False,
        "rss_modified":False,
        "publication_authorized":False
    }
    RESULT.parent.mkdir(parents=True,exist_ok=True)
    RESULT.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("FOUR_DETECTOR_SUMMARY",json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
