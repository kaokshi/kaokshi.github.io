#!/usr/bin/env python3
"""Three-genre, enlarged read-only research experiment.

NOT independently verified provenance. NOT a deployable calibrated classifier.
All samples are passed through four approved detectors. Pairs are partitioned
by source identity; raw copyrighted material is NEVER uploaded as artifacts.
"""
import argparse
import hashlib
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from editorial_ai_check import evaluate
from editorial_ensemble import wilson
from editorial_research_train import (
    SOURCE_FILES, SOURCE_REPO, SOURCE_REVISION, PILOT_ARTICLES,
    download_json, extract_pairs, four_features, predict,
    train_research, score_research,
)

SEED=20261120
COUNTS={"legends":(70,50), "science":(70,50), "cinema":(18,12)}
PROVENANCE="publisher_claim_not_independently_verified"
CORPUS_MOVIE_AI="seckur-tech/RuTextLLMDetect"
CORPUS_MOVIE_HUMAN="MihaoModea/kinopoisk"
CINEMA_SLUGS=("cinema-pilot-inception-20261120","cinema-pilot-shutter-island-20261120")


def russian_fraction(t):
    a=[c for c in t if c.isalpha()]
    return sum('а'<=c.lower()<='я' or c.lower()=='ё' for c in a)/len(a) if a else 0


def film_pairs():
    from datasets import load_dataset
    d=load_dataset(CORPUS_MOVIE_AI)
    pairs=[]
    for split, items in d.items():
        if not any(k in split.lower() for k in ("kino","movie","film")):
            continue
        for i,item in enumerate(items):
            h=item.get("summary")
            a=item.get("ai_text")
            if not all(isinstance(v,str) and len(v)>=350 and russian_fraction(v)>.75 for v in (h,a)):
                continue
            if h.strip()==a.strip():
                continue
            title=str(item.get("title","")).strip()
            # Group by film; avoid same film in train and holdout.
            key=hashlib.sha256((title or split+"/"+str(i)).encode()).hexdigest()
            pairs.append((key,h[:5500],a[:5500]))
    dedup={}
    seen_text=set()
    for key,h,a in pairs:
        if key in dedup or hashlib.sha256(h.encode()).hexdigest() in seen_text:
            continue
        seen_text.add(hashlib.sha256(h.encode()).hexdigest())
        dedup[key]=(key,h,a)
    return list(dedup.values())


def film_independent_human(max_count=60, exclude_groups=frozenset()):
    """External movie review stress test; NO automatic human label verification."""
    from datasets import load_dataset
    data=load_dataset(CORPUS_MOVIE_HUMAN,split="train")
    samples=[]
    groups=set()
    for i,item in enumerate(data):
        t=item.get("content")
        movie=str(item.get("movie_name") or "").strip()
        date=item.get("date")
        if not movie or movie in groups or not isinstance(t,str) or hashlib.sha256(movie.encode()).hexdigest() in exclude_groups:
            continue
        # Published user reviews predate contemporary LLMs; not absolute
        # authorship proof, but much stronger than recent unlabeled posts.
        if date is None or str(date)[:4] > "2012":
            continue
        if len(t)<700 or russian_fraction(t)<.8:
            continue
        groups.add(movie)
        samples.append(("external-human-"+hashlib.sha256(movie.encode()).hexdigest()[:16],t[:5500]))
        if len(samples)>=max_count:
            break
    if len(samples)<20:
        raise RuntimeError("Too few independent Russian long film reviews for stress testing")
    return samples


def evaluate_text(text, label, genre, sample_id, out_dir, split):
    tmp=out_dir/"ephemeral-samples"
    tmp.mkdir(parents=True,exist_ok=True)
    file=tmp/(hashlib.sha256((genre+sample_id+label).encode()).hexdigest()[:20]+".json")
    file.write_text(json.dumps({"slug":sample_id,"channel":genre,"body_html":text},ensure_ascii=False),encoding="utf-8")
    try:
        result=evaluate(file.resolve())
    finally:
        file.unlink(missing_ok=True)
    row={
        "source_id":sample_id,
        "label":label,
        "split":split,
        "features":four_features(result),
        "provenance_level":PROVENANCE,
        "text_sha256":hashlib.sha256(text.encode()).hexdigest()
    }
    print("SAMPLE_CHECKED",genre,split,label,sample_id[:16],flush=True)
    return row


def evaluate_pilots(genre, model):
    slugs=CINEMA_SLUGS if genre=="cinema" else PILOT_ARTICLES[genre]
    out=[]
    for slug in slugs:
        path=Path("content/articles")/(slug+".json")
        raw=path.read_bytes()
        result=evaluate(path.resolve())
        score=100*predict(model,{"features":four_features(result)})
        out.append({"slug":slug,"sha256_article_file":hashlib.sha256(raw).hexdigest(),
                    "research_index":round(score,2),
                    "note":"UNCALIBRATED_NOT_PERCENT_AI_AUTHORSHIP"})
    return out


def run(genre,out):
    ntrain,nhold=COUNTS[genre]
    if genre=="cinema":
        from editorial_cinema_pairs_augmented import generate_pairs
        pairs=film_pairs()
        additional=generate_pairs(n=20)
        existing_groups={p[0] for p in pairs}
        for extra in additional:
            if extra["pair_id"] in existing_groups:
                raise RuntimeError("Cinema group collision between corpora")
            existing_groups.add(extra["pair_id"])
            pairs.append((extra["pair_id"],extra["human"],extra["ai"]))
    else:
        first,second=SOURCE_FILES[genre]
        pairs=extract_pairs(download_json(first),download_json(second))
    random.Random(SEED).shuffle(pairs)
    if len(pairs)<ntrain+nhold:
        raise RuntimeError(f"Insufficient distinct matched {genre} groups: {len(pairs)} vs {ntrain+nhold}")
    rows=[]
    groups={"train":set(),"holdout":set()}
    seen_digests=set()
    for i,(key,h,a) in enumerate(pairs[:ntrain+nhold]):
        split="train" if i<ntrain else "holdout"
        if key in groups[split]:
            raise RuntimeError("Duplicate paired group")
        groups[split].add(key)
        for label,t in (("human",h),("ai",a)):
            r=evaluate_text(t,label,genre,key,out,split)
            if r["text_sha256"] in seen_digests:
                raise RuntimeError("Exact-text leakage across samples")
            seen_digests.add(r["text_sha256"])
            r["pair_id"]=key
            rows.append(r)
    if groups["train"] & groups["holdout"]:
        raise RuntimeError("Source group leakage")
    train=[r for r in rows if r["split"]=="train"]
    holdout=[r for r in rows if r["split"]=="holdout"]
    model=train_research(train,n_train_pairs=ntrain)
    stats=score_research(model,holdout,n_holdout_pairs=nhold)
    # Explicit owner-selected future routing threshold, measured out-of-sample.
    tp=sum(predict(model,r)*100>30 for r in holdout if r["label"]=="ai")
    fp=sum(predict(model,r)*100>30 for r in holdout if r["label"]=="human")
    stats["future_cutoff_30"]={
        "human_false_alarms":fp,"human_n":nhold,
        "ai_detected":tp,"ai_n":nhold,
        "false_alarm_95pct":wilson(fp,nhold),
        "detection_95pct":wilson(tp,nhold),
        "cutoff_validated":False
    }
    stress=None
    if genre=="cinema":
        # External human material: used ONLY in holdout, never to fit the classifier.
        source=film_independent_human(exclude_groups={p[0] for p in pairs})
        source_trainhash={r["text_sha256"] for r in train}
        stress_rows=[]
        for key,t in source:
            if hashlib.sha256(t.encode()).hexdigest() in source_trainhash:
                raise RuntimeError("External stress overlap")
            stress_rows.append(evaluate_text(t,"human",genre,key,out,"external_stress"))
        f=sum(predict(model,r)*100>30 for r in stress_rows)
        stress={"n_human":len(stress_rows),"cutoff30_false_alarms":f,
                "cutoff30_false_alarm_95pct":wilson(f,len(stress_rows)),
                "data_note":"Movie reviews not plot recaps; publisher-origin labels; never release-validation"}
    pilots=evaluate_pilots(genre,model)
    summary={
        "schema":"four-detector-genre-training-v2",
        "genre":genre,
        "status":"research_trained_unvalidated",
        "paired_source":"10 public demo paired movie reviews plus 20 new generated dated-movie pairs" if genre=="cinema" else SOURCE_REPO,
        "pair_count_available":len(pairs),
        "cinema_pair_provenance": {
            "public_cinema_demo_pairs": 10,
            "self_generated_known_model_pairs": 20,
            "generator": "ai-forever/rugpt3small_based_on_gpt2",
            "human_source": "MihaoModea/kinopoisk pre-2013 reviews",
            "human_label_independently_verified": False,
            "machine_label_from_instrumented_generator": True,
            "film_review_not_long_recap": True,
        } if genre=="cinema" else None,
        "n_train":len(train),"n_holdout":len(holdout),
        "train_pair_groups":len(groups["train"]),"holdout_pair_groups":len(groups["holdout"]),
        "metrics":stats,
        "external_movie_human_stress_test":stress,
        "pilots":pilots,
        "threshold30_activated":False,
        "provenance_verified_independently":False,
        "training_base_model_data_overlap_excluded":False,
        "release_authorized":False,
        "note":"A score from a review-domain model must not be interpreted as a calibrated authorship probability on long Russian plot recaps."
    }
    model["genre"]=genre
    model["training_kind"]="research_publisher_labels"
    (out/(genre+"-research-model.json")).write_text(json.dumps(model,indent=2)+"\n",encoding="utf-8")
    (out/(genre+"-research-summary.json")).write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("RESEARCH_V2_RESULT",json.dumps(summary,ensure_ascii=False),flush=True)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--genre",required=True,choices=tuple(COUNTS))
    p.add_argument("--out",default="editorial-training-v2")
    args=p.parse_args()
    out=Path(args.out).resolve()
    out.mkdir(parents=True,exist_ok=True)
    try:
        run(args.genre,out)
    finally:
        for file in (out/"ephemeral-samples").glob("*.json"):
            file.unlink()


if __name__=="__main__":
    main()
