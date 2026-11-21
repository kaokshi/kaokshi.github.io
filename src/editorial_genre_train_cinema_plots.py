#!/usr/bin/env python3
"""Research cinema training on Russian Wikipedia *plots*, never short film reviews.

Generation (private, separate env) -> detector scoring (fresh Python process).
Provenance is Wikipedia editor claims + instrumented ruGPT generator; not an
independently validated human authorship dataset. No release approval.
"""
import argparse
import hashlib
import json
import os
import random
from pathlib import Path

GENRE = "cinema"
SOURCE = "B1KO1L/movie-plots-from-wikipedia-in-russian"
MODEL = "ai-forever/rugpt3small_based_on_gpt2"
SEED = 20261120
TRAIN_PAIRS = 35
HOLDOUT_PAIRS = 25
TOTAL_PAIRS = TRAIN_PAIRS + HOLDOUT_PAIRS


def russian_ratio(text):
    chars = [ch for ch in text if ch.isalpha()]
    return sum("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in chars) / len(chars) if chars else 0.0


def plot_candidates():
    from datasets import load_dataset
    from huggingface_hub import HfApi

    revision = HfApi().dataset_info(SOURCE).sha
    rows = load_dataset(SOURCE, split="train", revision=revision, streaming=True)
    found, titles, digests = [], set(), set()
    for row in rows:
        title = str(row.get("title") or "").strip()
        plot = row.get("plot")
        if (not title or title.lower() in titles
                or not isinstance(plot, str) or len(plot) < 1100
                or russian_ratio(plot) < .8):
            continue
        plot = plot[:4700]
        digest = hashlib.sha256(" ".join(plot.lower().split()).encode()).hexdigest()
        if digest in digests:
            continue
        titles.add(title.lower())
        digests.add(digest)
        found.append((title, plot))
    random.Random(SEED).shuffle(found)
    if len(found) < TOTAL_PAIRS + 60:
        raise RuntimeError(f"Too few eligible Wikipedia film plots: {len(found)}")
    print("CINEMA_PLOT_CANDIDATES",len(found),"source_revision",revision,flush=True)
    return found, revision


def generate(private_file):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed

    candidates, revision = plot_candidates()
    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    generator = AutoModelForCausalLM.from_pretrained(MODEL)
    generator.eval()
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    pairs = []
    with torch.inference_mode():
        for n,(title,plot) in enumerate(candidates):
            if len(pairs) >= TOTAL_PAIRS:
                break
            # NO human plot text in the prompt. We measure authorship style,
            # not a rewriting/paraphrasing detector.
            prompt = f"Подробный пересказ сюжета фильма «{title}». События фильма:\n"
            encoded = tokenizer(prompt,return_tensors="pt",truncation=True,max_length=145)
            produced = ""
            for attempt in range(3):
                set_seed(SEED+n*13+attempt)
                generated = generator.generate(
                    **encoded, do_sample=True, top_p=.92, top_k=45,
                    temperature=.85, max_new_tokens=380,
                    pad_token_id=tokenizer.eos_token_id,
                )
                candidate = tokenizer.decode(
                    generated[0][encoded.input_ids.shape[1]:],skip_special_tokens=True
                ).strip()
                if len(candidate) >= 500 and russian_ratio(candidate) >= .73:
                    produced = candidate
                    break
            if not produced:
                continue
            # Length matching avoids a trivial difference of document length.
            human_text = plot[:len(produced)]
            if len(human_text) < 500:
                continue
            group = hashlib.sha256(title.lower().encode()).hexdigest()
            pairs.append({
                "pair_id": group, "human": human_text,
                "ai": produced, "title_hash": group[:12],
                "human_length": len(human_text),
                "ai_length": len(produced),
                "human_source": SOURCE,
                "model_source": MODEL
            })
            if len(pairs)%10 == 0:
                print("CINEMA_PLOT_PAIRS",len(pairs),"/",TOTAL_PAIRS,flush=True)
    if len(pairs) != TOTAL_PAIRS:
        raise RuntimeError(f"Generated {len(pairs)} of {TOTAL_PAIRS} target cinema pairs")
    private_file.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(private_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as writer:
        json.dump({"revision":revision,"pairs":pairs},writer,ensure_ascii=False)
    print("CINEMA_PLOTS_READY",len(pairs),flush=True)


def train(private_file,output):
    from editorial_ensemble import wilson
    from editorial_research_train import predict, train_research, score_research
    from editorial_research_v2 import evaluate_text, evaluate_pilots

    if not private_file.is_file():
        raise RuntimeError("No private plots to train")
    payload=json.loads(private_file.read_text(encoding="utf-8"))
    private_file.unlink()
    rows=payload.get("pairs") or []
    if len(rows)!=TOTAL_PAIRS:
        raise RuntimeError("Incorrect plot pairing count")
    random.Random(SEED+99).shuffle(rows)
    output.mkdir(parents=True,exist_ok=True)
    train_rows,holdout_rows=[],[]
    seen=set()
    try:
        for i,pair in enumerate(rows):
            split="train" if i<TRAIN_PAIRS else "holdout"
            key=pair["pair_id"]
            if key in seen:
                raise RuntimeError("Plot group leakage")
            seen.add(key)
            for label,text in (("human",pair["human"]),("ai",pair["ai"])):
                data=evaluate_text(text,label,GENRE,key,output,split)
                data["pair_id"]=key
                (train_rows if split=="train" else holdout_rows).append(data)
            if (i+1)%10==0:
                print("CINEMA_PLOT_EVALUATED",i+1,flush=True)
        model=train_research(train_rows,n_train_pairs=TRAIN_PAIRS)
        metrics=score_research(model,holdout_rows,n_holdout_pairs=HOLDOUT_PAIRS)
        fp=sum(predict(model,r)*100>30 for r in holdout_rows if r["label"]=="human")
        tp=sum(predict(model,r)*100>30 for r in holdout_rows if r["label"]=="ai")
        metrics["planned_cutoff30"]={
            "false_alarms":fp, "human_n":HOLDOUT_PAIRS,
            "ai_detected":tp, "ai_n":HOLDOUT_PAIRS,
            "fpr_wilson95":wilson(fp,HOLDOUT_PAIRS),
            "tpr_wilson95":wilson(tp,HOLDOUT_PAIRS),
            "validated":False,
        }
        report={
            "schema":"cinema-wikipedia-plots-genre-research-v1",
            "genre":"cinema", "source":SOURCE,
            "source_revision":payload["revision"],
            "model_generator":MODEL,
            "n_train":len(train_rows),
            "n_holdout":len(holdout_rows),
            "human_metadata_independently_verified":False,
            "base_detector_overlap_excluded":False,
            "genre_fit":"movie plot rather than review, but generated mini recaps may hallucinate",
            "length_matched":True,
            "threshold_enabled":False,
            "publication_authorized":False,
            "status":"research_only_unvalidated",
            "metrics":metrics,
            "pilots":evaluate_pilots(GENRE,model)
        }
        model.update({"genre":GENRE,"status":"research_only_unvalidated",
                      "publication_authorized":False,"threshold_enabled":False})
        (output/"cinema-plots-model.json").write_text(json.dumps(model,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        (output/"cinema-plots-summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("CINEMA_PLOTS_RESEARCH_RESULT",json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        tmp=output/"ephemeral-samples"
        if tmp.exists():
            for file in tmp.glob("*.json"):
                file.unlink()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=("generate","train"))
    parser.add_argument("--private-file",required=True)
    parser.add_argument("--out",default="editorial-cinema-plots")
    args=parser.parse_args()
    path=Path(args.private_file).resolve()
    if args.mode=="generate":
        generate(path)
    else:
        train(path,Path(args.out).resolve())


if __name__=="__main__":
    main()
