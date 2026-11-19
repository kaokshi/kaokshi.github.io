#!/usr/bin/env python3
"""Run pinned ruBERT v4 model for a JSON report. Never changes any publication state."""
import argparse
import json
from pathlib import Path
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="UTF-8 text input")
    parser.add_argument("--source", required=True, help="Absolute path to cloned upstream ru-ai-text-detector")
    args = parser.parse_args()
    src = Path(args.source).resolve()
    if not (src / "aidetector" / "transformer_score.py").is_file():
        raise SystemExit("Pinned ruBERT source is missing")
    sys.path.insert(0, str(src))
    from aidetector.transformer_score import get, word_count
    text = Path(args.file).read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("Text is empty")
    scorer = get("v4")
    probability = float(scorer.proba([text], bs=1)[0])
    threshold = float(scorer.threshold_for(word_count(text)))
    if not 0 <= probability <= 1:
        raise SystemExit("Invalid calibrated model value")
    print(json.dumps({
        "model": "ruBERT v4",
        "probability": probability,
        "score_percent": round(probability * 100, 4),
        "model_native_threshold": threshold,
        "editorial_limit_percent": 10,
        "word_count": word_count(text),
        "prediction_native": "ai" if probability >= threshold else "human",
        "note": "The calibrated score is not proof of authorship. The 10% limit is an editorial preference, not the model's native classification threshold."
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
