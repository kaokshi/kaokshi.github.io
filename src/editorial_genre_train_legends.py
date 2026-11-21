#!/usr/bin/env python3
"""Large genre-specific, four-detector literary experiment (RESEARCH ONLY).

Uses long-form Russian literary human/AI labeled data with >=3 spooky lexical
cues. Keyword matching is imperfect: this is a genre proxy, not a verified
collection of supernatural narratives. Source labels remain UNVERIFIED.
No external text enters git, RSS, artifact uploads or report logs.
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

from editorial_genre_corpus_probe import GHOST_WORDS, LITERARY, check_length, horror_match
from editorial_research_v2 import evaluate_text, evaluate_pilots
from editorial_research_train import predict, score_research, train_research
from editorial_ensemble import wilson

SEED = 20261120
N_TRAIN_PER_CLASS = 100
N_HOLDOUT_PER_CLASS = 100
GENRE = "legends"
SOURCE = LITERARY


def candidates():
    from datasets import load_dataset
    from huggingface_hub import HfApi

    info = HfApi().dataset_info(SOURCE)
    revision = info.sha
    ds = load_dataset(SOURCE, split="train", revision=revision, streaming=True)
    buckets = {"human": {}, "ai": {}}
    for i, row in enumerate(ds):
        text = row.get("text")
        label = row.get("label")
        if not (check_length(text) and horror_match(text)):
            continue
        if label in (0, "0", "human", "Human"):
            name = "human"
        elif label in (1, "1", "ai", "AI"):
            name = "ai"
        else:
            continue
        normalized = " ".join(text.lower().split())
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        buckets[name].setdefault(digest, {
            "source_id": digest, "text": text, "source_position": i
        })
    counts = {name: len(entries) for name, entries in buckets.items()}
    if min(counts.values()) < N_TRAIN_PER_CLASS + N_HOLDOUT_PER_CLASS:
        raise RuntimeError("Not enough DISTINCT genre-filtered samples by class: " + str(counts))
    selected = {}
    for name, entries in buckets.items():
        values = list(entries.values())
        random.Random(SEED + (0 if name == "human" else 1001)).shuffle(values)
        selected[name] = values[:N_TRAIN_PER_CLASS + N_HOLDOUT_PER_CLASS]
    return selected, counts, revision


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="editorial-expanded-legends")
    args = p.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    selected, counts, revision = candidates()
    seen = set()
    training, holdout = [], []
    try:
        for split, begin, end in (
            ("train", 0, N_TRAIN_PER_CLASS),
            ("holdout", N_TRAIN_PER_CLASS, N_TRAIN_PER_CLASS + N_HOLDOUT_PER_CLASS)
        ):
            for label in ("human", "ai"):
                for j, sample in enumerate(selected[label][begin:end]):
                    key = sample["source_id"]
                    if key in seen:
                        raise RuntimeError("Duplicate across splits or classes")
                    seen.add(key)
                    row = evaluate_text(sample["text"], label, GENRE, key, out, split)
                    row["pair_id"] = "unmatched-label/" + key
                    (training if split == "train" else holdout).append(row)
                    if (j + 1) % 20 == 0:
                        print("GENRE_PROGRESS", split, label, j + 1, flush=True)
        model = train_research(training, n_train_pairs=N_TRAIN_PER_CLASS)
        stats = score_research(model, holdout, n_holdout_pairs=N_HOLDOUT_PER_CLASS)
        fp = sum(100 * predict(model, r) > 30 for r in holdout if r["label"] == "human")
        tp = sum(100 * predict(model, r) > 30 for r in holdout if r["label"] == "ai")
        stats["future_cutoff_30"] = {
            "human_false_alarms": fp, "human_n": N_HOLDOUT_PER_CLASS,
            "ai_detected": tp, "ai_n": N_HOLDOUT_PER_CLASS,
            "human_false_alarm_95pct": wilson(fp, N_HOLDOUT_PER_CLASS),
            "ai_detection_95pct": wilson(tp, N_HOLDOUT_PER_CLASS),
            "cutoff_validated": False
        }
        pilots = evaluate_pilots(GENRE, model)
        report = {
            "schema": "genre-specific-literary-proxy-experiment-v1",
            "genre": GENRE, "source": SOURCE, "dataset_commit": revision,
            "candidate_counts": counts, "keyword_cues": list(GHOST_WORDS),
            "genre_filter": "longform Russian fiction with >=3 spooky lexical cues",
            "training_examples": len(training), "holdout_examples": len(holdout),
            "holdout_independently_validated": False,
            "labels_independently_verified": False,
            "base_detector_overlap_excluded": False,
            "author_group_overlap_excluded": False,
            "matched_source_pairs": False,
            "threshold_enabled": False,
            "publication_authorized": False,
            "status": "research_only_unvalidated",
            "metrics": stats,
            "pilots": pilots,
            "caveat": ("Genre keyword filter and publisher labels are not verified ghost stories "
                       "or reliable human authorship. Holdout shares source dataset; "
                       "this cannot serve as final independent validation.")
        }
        model.update({
            "genre": GENRE, "status": "research_only_publisher_labels_unverified",
            "source_revision": revision, "threshold_enabled": False,
            "publication_authorized": False
        })
        (out / "legends-expanded-model.json").write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
        (out / "legends-expanded-summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("GENRE_EXPANDED_RESULT", json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        tmp = out / "ephemeral-samples"
        if tmp.exists():
            for path in tmp.glob("*.json"):
                path.unlink()


if __name__ == "__main__":
    main()
