#!/usr/bin/env python3
"""Research ONLY: pair-matched, publisher-labelled Russian four-detector benchmark.

Never implies independently verified human authorship, never emits a production
model or writes content/articles, RSS, site config, or ai_revision_policy.
"""
import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen

from editorial_ai_check import evaluate
from editorial_ensemble import FEATURES, sigmoid, wilson

SOURCE_REPO = "CoffeBank/Ru-hard-detection-dataset"
SOURCE_REVISION = "2d6477427de88ec8ad86698917fe16f49a556b81"
SOURCE_FILES = {
    "legends": ("main/essay/original_essay.json", "main/essay/generated_essays.json"),
    "science": ("main/scientific_texts/orig_scientific.json", "main/scientific_texts/generated_scientific.json"),
}
GENRES = ("legends", "science")
PILOT_ARTICLES = {
    "legends": ("legends-pilot-midnight-bus-20261120", "legends-pilot-photo-studio-20261120"),
    "science": ("science-pilot-noctilucent-clouds-20261120", "science-pilot-hair-ice-20261120"),
}
# Corpus essays are a poor proxy for original horror; film has no verified training set.
N_TRAIN_PAIRS = 20
N_HOLDOUT_PAIRS = 10
MIN_CHARS = 700
MAX_CHARS = 5500


def download_json(path):
    import urllib.parse
    if path not in {p for pair in SOURCE_FILES.values() for p in pair}:
        raise ValueError("Refusing unapproved corpus path")
    url = f"https://raw.githubusercontent.com/{SOURCE_REPO}/{SOURCE_REVISION}/{path}"
    with urlopen(url, timeout=120) as response:
        payload = response.read(30_000_000)
        if response.read(1):
            raise ValueError("Corpus exceeds hard size limit")
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("Expected JSON array")
    return data


def extract_pairs(humans, generated):
    def grouped(items):
        out = defaultdict(list)
        for x in items:
            if isinstance(x, dict) and isinstance(x.get("text"), str) and "id" in x:
                out[str(x["id"])].append(x)
        return out
    h = grouped(humans)
    a = grouped(generated)
    result = []
    for idx in sorted(h.keys() & a.keys()):
        human = next((x for x in h[idx] if str(x.get("source", "")).lower() == "human"), None)
        ai = next((x for x in a[idx] if str(x.get("source", "")).lower() == "ai"), None)
        if human is None or ai is None:
            continue
        human_text = human["text"].strip()[:MAX_CHARS]
        ai_text = ai["text"].strip()[:MAX_CHARS]
        if min(len(human_text), len(ai_text)) < MIN_CHARS:
            continue
        if not all(0.5 <= sum("а" <= c.lower() <= "я" or c in "Ёё" for c in t) / max(1, sum(c.isalpha() for c in t)) <= 1 for t in (human_text, ai_text)):
            continue
        if human_text == ai_text:
            continue
        result.append((idx, human_text, ai_text))
    return result


def four_features(data):
    if not data.get("all_checks_completed"):
        raise RuntimeError("Four-detector job incomplete")
    checks = data["checks"]
    r = checks["rubert_v4"]["data"]
    m = checks["modernbert"]["data"]
    l = checks["lightgbm"]["data"]
    a = checks["ai_accent_detector"]["data"]
    features = {
        "rubert_v4": r.get("score_percent"),
        "modernbert": 100 * m["probability"],
        "lightgbm": 100 * l["probability"],
        "ai_accent_detector": 100 * a["ai_accent_score"],
    }
    if any(not isinstance(v, (float, int)) or not math.isfinite(v) or v < 0 or v > 100 for v in features.values()):
        raise RuntimeError("Nonfinite model feature")
    return {k: round(float(v), 5) for k, v in features.items()}


def train_research(rows, n_train_pairs=N_TRAIN_PAIRS):
    """Logistic meta-classifier using publisher labels only, not production fit()."""
    if len(rows) != 2 * n_train_pairs:
        raise ValueError("Insufficient paired training rows")
    if any(row["provenance_level"] != "publisher_claim_not_independently_verified" for row in rows):
        raise ValueError("Unexpected provenance status")
    weights = [0.0] * 5
    for _ in range(900):
        gradient = [0.0] * 5
        for row in rows:
            x = [1.] + [row["features"][n] / 100 for n in FEATURES]
            y = int(row["label"] == "ai")
            diff = sigmoid(sum(v*w for v, w in zip(x, weights))) - y
            for j in range(5):
                gradient[j] += diff * x[j]
        for j in range(5):
            gradient[j] = gradient[j]/len(rows) + (0 if j == 0 else 0.01*weights[j])
            weights[j] -= .15 * gradient[j]
    return {"status": "research_only_publisher_labels_unverified", "features": list(FEATURES),
            "coefficients": weights, "n_train": len(rows), "publication_authorized": False}


def predict(model, row):
    x = [1.] + [row["features"][n]/100 for n in FEATURES]
    return sigmoid(sum(v*w for v, w in zip(x, model["coefficients"])))


def score_research(model, holdout, n_holdout_pairs=N_HOLDOUT_PAIRS):
    if len(holdout) != 2 * n_holdout_pairs:
        raise ValueError("Insufficient matched evaluation samples")
    human = [r for r in holdout if r["label"] == "human"]
    ai = [r for r in holdout if r["label"] == "ai"]
    values = [(predict(model, row), int(row["label"] == "ai")) for row in holdout]
    fp = sum(p > .5 for p, y in values if y == 0)
    tp = sum(p > .5 for p, y in values if y == 1)
    brier = sum((p-y)**2 for p, y in values)/len(values)
    return {"n_human": len(human), "n_ai": len(ai), "false_alarms": fp,
            "ai_detected": tp, "false_alarm_rate": fp/len(human),
            "detection_rate": tp/len(ai),
            "human_false_alarm_95pct": wilson(fp, len(human)),
            "ai_detection_95pct": wilson(tp, len(ai)),
            "brier": round(brier, 5),
            "unvalidated": True,
            "publication_authorized": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args=parser.parse_args()
    out=Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report={"schema": "four-detector-provisional-research-v1",
            "source_revision": SOURCE_REVISION,
            "source_repository": SOURCE_REPO,
            "source_labels": "publisher_claim_not_independently_verified",
            "independent_validation": False,
            "auto_release_authorized": False,
            "cinema": {"status": "not_trained", "reason": "No audited relevant Russian film human/AI paired corpus selected"},
            "genres": {}}
    for genre in GENRES:
        original, generated = SOURCE_FILES[genre]
        pairs = extract_pairs(download_json(original), download_json(generated))
        random.Random(20261120).shuffle(pairs)
        if len(pairs) < N_TRAIN_PAIRS + N_HOLDOUT_PAIRS:
            report["genres"][genre]={"status": "insufficient_pair_matched_corpus", "available":len(pairs)}
            continue
        chosen = pairs[:N_TRAIN_PAIRS + N_HOLDOUT_PAIRS]
        rows = []
        temp = (out/"tmp").resolve()
        temp.mkdir(exist_ok=True)
        try:
            for i, (pairid, human, ai) in enumerate(chosen):
                split="train" if i < N_TRAIN_PAIRS else "holdout"
                for label, raw in (("human",human),("ai",ai)):
                    suffix = hashlib.sha256((genre+pairid+label).encode()).hexdigest()[:16]
                    path=temp/(suffix+".json")
                    # Text owned by external dataset is used only inside the ephemeral runner.
                    path.write_text(json.dumps({"slug":suffix,"channel":genre,"body_html":raw},ensure_ascii=False),encoding="utf-8")
                    result=evaluate(path)
                    path.unlink(missing_ok=True)
                    features=four_features(result)
                    rows.append({"source_id":f"{SOURCE_REPO}/{genre}/{pairid}/{label}",
                                 "pair_id":pairid,"label":label,"split":split,
                                 "provenance_level":"publisher_claim_not_independently_verified",
                                 "text_sha256":hashlib.sha256(raw.encode()).hexdigest(),
                                 "features":features})
                    print("RESEARCH_EVALUATED", genre, split, label, i+1, flush=True)
            tr=[r for r in rows if r["split"]=="train"]
            te=[r for r in rows if r["split"]=="holdout"]
            train_ids={r["pair_id"] for r in tr}
            assert not train_ids.intersection(r["pair_id"] for r in te)
            model=train_research(tr)
            stats=score_research(model,te)
            pilot_indices = []
            for slug in PILOT_ARTICLES[genre]:
                article_path = Path("content/articles") / (slug + ".json")
                if not article_path.is_file():
                    raise RuntimeError("Missing pilot for scoring: " + slug)
                diagnostic = evaluate(article_path.resolve())
                row = {"features": four_features(diagnostic)}
                pilot_indices.append({
                    "slug": slug,
                    "article_sha256": hashlib.sha256(article_path.read_bytes()).hexdigest(),
                    "provisional_index_0_100": round(100 * predict(model, row), 2),
                    "status": "RESEARCH_NOT_CALIBRATED_NOT_AI_AUTHORSHIP_PROBABILITY",
                })
            report["genres"][genre]={"status":"provisional_trained_and_evaluated",
                                      "domain_warning": "essay is not supernatural fiction" if genre=="legends" else None,
                                      "train_n":len(tr),"holdout_n":len(te),
                                      "evaluation":stats,
                                      "pilot_indices": pilot_indices}
            (out/(genre+"-RESEARCH-ONLY.json")).write_text(json.dumps(model,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        finally:
            for p in temp.glob("*.json"):
                p.unlink()
    dest=out/"research_summary.json"
    dest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("RESEARCH_FINAL",json.dumps(report,ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
