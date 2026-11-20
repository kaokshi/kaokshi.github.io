#!/usr/bin/env python3
"""Research-only 4-detector science expansion on labelled Russian scientific prose.

This is scientific-text domain, not yet popular-science journalism. Publisher
human/AI claims are NOT independently audited. Writes only aggregate metrics.
"""
import argparse
import hashlib
import json
import random
from pathlib import Path

from editorial_ensemble import wilson
from editorial_research_train import SOURCE_FILES, SOURCE_REPO, download_json, extract_pairs, predict, train_research, score_research
from editorial_research_v2 import evaluate_text, evaluate_pilots

GENRE = "science"
SEED = 20261120
TRAIN_PAIRS = 110
HOLDOUT_PAIRS = 90


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="editorial-expanded-science")
    args = ap.parse_args()
    dest = Path(args.out).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    human, machine = SOURCE_FILES[GENRE]
    pairs = extract_pairs(download_json(human), download_json(machine))
    random.Random(SEED + 170).shuffle(pairs)
    target = TRAIN_PAIRS + HOLDOUT_PAIRS
    if len(pairs) < target:
        raise RuntimeError(f"Not enough science pairs: {len(pairs)} < {target}")
    training, holdout, seen = [], [], set()
    try:
        for i, (source_id, human_text, ai_text) in enumerate(pairs[:target]):
            split = "train" if i < TRAIN_PAIRS else "holdout"
            for label, content in (("human", human_text), ("ai", ai_text)):
                digest = hashlib.sha256(content.encode()).hexdigest()
                if digest in seen:
                    raise RuntimeError("Repeated text across training/test")
                seen.add(digest)
                row = evaluate_text(content, label, GENRE, source_id, dest, split)
                row["pair_id"] = source_id
                (training if split == "train" else holdout).append(row)
            if (i + 1) % 20 == 0:
                print("SCIENCE_PAIR_PROGRESS", i + 1, "of", target, flush=True)
        model = train_research(training, n_train_pairs=TRAIN_PAIRS)
        metrics = score_research(model, holdout, n_holdout_pairs=HOLDOUT_PAIRS)
        fp = sum(predict(model, row) * 100 > 30 for row in holdout if row["label"] == "human")
        tp = sum(predict(model, row) * 100 > 30 for row in holdout if row["label"] == "ai")
        metrics["planned_cutoff_30"] = {
            "false_alarms": fp, "human_n": HOLDOUT_PAIRS,
            "ai_detected": tp, "ai_n": HOLDOUT_PAIRS,
            "fpr_wilson95": wilson(fp, HOLDOUT_PAIRS),
            "tpr_wilson95": wilson(tp, HOLDOUT_PAIRS),
            "validated": False
        }
        pilots = evaluate_pilots(GENRE, model)
        result = {
            "schema": "science-longtext-proxy-v1", "genre": GENRE,
            "status": "research_only_unvalidated",
            "source": SOURCE_REPO,
            "source_domain": "Russian scientific text, NOT independently vetted popular science",
            "n_train": len(training), "n_research_holdout": len(holdout),
            "provenance_verified": False,
            "base_model_training_overlap_ruled_out": False,
            "threshold_enabled": False,
            "publication_authorized": False,
            "metrics": metrics, "pilots": pilots
        }
        model.update({"genre": GENRE, "status": "research_only_unvalidated",
                      "publication_authorized": False, "threshold_enabled": False})
        (dest/"science-expanded-model.json").write_text(json.dumps(model,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        (dest/"science-expanded-summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("SCIENCE_EXPANDED_RESULT", json.dumps(result,ensure_ascii=False),flush=True)
    finally:
        temp = dest/"ephemeral-samples"
        if temp.exists():
            for file in temp.glob("*.json"):
                file.unlink()


if __name__ == "__main__":
    main()
