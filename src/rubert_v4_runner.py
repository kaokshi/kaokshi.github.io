#!/usr/bin/env python3
"""Pinned ruBERT v4 diagnostics on successive short text windows.

The upstream encoder truncates at 256 model tokens. This runner uses smaller
overlapping windows, reporting a conservative maximum to avoid false certainty
from checking only the start of a long article. It does not authorize release.
"""
import argparse
import json
from pathlib import Path
import re
import sys

WINDOW_WORDS = 85
STRIDE_WORDS = 65


def windows(text):
    words = re.findall(r"\S+", text)
    if not words:
        return []
    if len(words) <= WINDOW_WORDS:
        return [" ".join(words)]
    starts = list(range(0, len(words) - WINDOW_WORDS + 1, STRIDE_WORDS))
    if starts[-1] != len(words) - WINDOW_WORDS:
        starts.append(len(words) - WINDOW_WORDS)
    return [" ".join(words[start:start + WINDOW_WORDS]) for start in starts]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="UTF-8 text input")
    parser.add_argument("--source", required=True, help="Absolute path to cloned pinned ruBERT source")
    args = parser.parse_args()
    source = Path(args.source).resolve()
    if not (source / "aidetector" / "transformer_score.py").is_file():
        raise SystemExit("Pinned ruBERT source is missing")
    sys.path.insert(0, str(source))
    from aidetector.transformer_score import get, word_count
    text = Path(args.file).read_text(encoding="utf-8").strip()
    segments = windows(text)
    if not segments:
        raise SystemExit("Text is empty")
    scorer = get("v4")
    values = [float(p) for p in scorer.proba(segments, bs=1)]
    if any(not 0 <= p <= 1 for p in values):
        raise SystemExit("Invalid calibrated model values")
    thresholds = [float(scorer.threshold_for(word_count(segment))) for segment in segments]
    # The editorial gate uses the highest flagged window, not a mean that
    # could hide an anomalous passage. This is a house rule, not model calibration.
    primary = max(values)
    print(json.dumps({
        "model": "ruBERT v4",
        "probability": primary,
        "score_percent": round(primary * 100, 4),
        "aggregation": "max_window_probability",
        "model_window_words": WINDOW_WORDS,
        "model_window_stride_words": STRIDE_WORDS,
        "window_count": len(segments),
        "window_probabilities": [round(p, 6) for p in values],
        "mean_probability": sum(values) / len(values),
        "model_native_threshold": min(thresholds),
        "editorial_limit_percent": 25,
        "word_count": word_count(text),
        "prediction_native": "ai" if any(p >= t for p, t in zip(values, thresholds)) else "human",
        "note": "The max-over-windows score is a conservative editor rule, not a calibrated whole-document probability. Longer texts have more chances to contain a high-scoring window. The 25% cutoff is a house preference and not proof of authorship."
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
