#!/usr/bin/env python3
"""Automatic offline disagreement triage for the four existing AI checkers.

Advisory only: no authorship assertions, no rewrite trigger, no release flags.
"""
import argparse
import json
import math
from pathlib import Path

MODELS = ("rubert_v4", "modernbert", "lightgbm", "ai_accent_detector")
CLASSIFIERS = ("rubert_v4", "modernbert", "lightgbm")

def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100

def extract_scores(report):
    result = {}
    diagnostics = report.get("editorial_threshold", {}).get("checks", {})
    for name in MODELS:
        item = diagnostics.get(name, {})
        val = item.get("index_0_100") if item.get("status") == "completed" else None
        result[name] = float(val) if numeric(val) else None
    return result

def triage(report):
    scores = extract_scores(report)
    missing = [name for name, value in scores.items() if value is None]
    text_length = report.get("characters_without_spaces")
    if not report.get("all_checks_completed") or missing:
        return {
            "status": "incomplete", "routing": "human_editorial_review",
            "model_scores": scores, "missing": missing,
            "reasons": ["incomplete_actual_detector_reports"],
            "needs_text_rewrite": False, "publication_authorized": False
        }
    classifiers = [scores[key] for key in CLASSIFIERS]
    spread = round(max(classifiers) - min(classifiers), 2)
    reasons = []
    # Disagreement is a *diagnostic* alert, not AI probability.
    if spread >= 50:
        reasons.append("large_cross_model_disagreement")
    if not isinstance(text_length, int) or text_length < 3000:
        reasons.append("text_length_needs_editorial_check")
    return {
        "status": "completed",
        "routing": "editorial_attention" if reasons else "routine_quality_checks",
        "model_scores": scores,
        "classifier_spread_points": spread,
        "style_signal": scores["ai_accent_detector"],
        "reasons": reasons,
        "needs_text_rewrite": False,
        "publication_authorized": False,
        "notes": [
            "Large model disagreement is not evidence of a flawed article.",
            "Only concrete grammar, factual, logical or source defects warrant revision.",
            "Routine checks do not imply any publication approval."
        ]
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", default="editorial-check-reports")
    parser.add_argument("--output", default="editorial-ai-triage")
    args = parser.parse_args()
    folder = Path(args.reports)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for file in sorted(folder.glob("*.json")):
        if file.name == "index.json":
            continue
        report = json.loads(file.read_text(encoding="utf-8"))
        assessment = triage(report)
        assessment.update({"article_slug": report.get("article_slug"), "channel": report.get("channel")})
        summaries.append(assessment)
        (output / file.name).write_text(json.dumps(assessment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "index.json").write_text(json.dumps({"schema": "offline-ai-triage-v1",
                    "articles": summaries, "publication_authorized": False}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"articles": len(summaries),
        "editorial_attention": sum(x["routing"] == "editorial_attention" for x in summaries),
        "incomplete": sum(x["status"] == "incomplete" for x in summaries)}, ensure_ascii=False))
    if not summaries:
        raise SystemExit("No actual AI checker reports to evaluate")

if __name__ == "__main__":
    main()
