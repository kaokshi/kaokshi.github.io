#!/usr/bin/env python3
"""Offline, read-only comparison of two CPU candidates replacing ruBERT v4.

One candidate per ensemble: [modernbert, lightgbm, candidate].
Their arithmetic mean is an illustrative index, NOT calibrated authorship %.
AI Accent is a separate style diagnostic, not a classifier probability.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

from editorial_ai_check import extract_text, selected_articles, tool_result

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ("transformer", "mini-frida")
CLASSIFIERS = ("modernbert", "lightgbm")
ALL_MODELS = CLASSIFIERS + CANDIDATES

def score_from_tool(result):
    if not isinstance(result, dict) or result.get("status") != "completed":
        return None
    payload = result.get("data", {})
    # aiw-ru classify --json (v2.5.2) returns 0..1 in `probability`,
    # not `score_percent`. Never read a nested fragment probability as whole text.
    if not isinstance(payload, dict) or payload.get("skipped") is True:
        return None
    val = payload.get("probability")
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not 0 <= val <= 1:
        return None
    return round(float(val) * 100, 4)

def merge_results(raw):
    values = {name: score_from_tool(raw.get(name)) for name in ALL_MODELS}
    style = raw.get("ai_accent_detector", {})
    accent_data = style.get("data", {}) if style.get("status") == "completed" else {}
    accent = accent_data.get("ai_accent_score") if isinstance(accent_data, dict) else None
    if isinstance(accent, bool) or not isinstance(accent, (int, float)) or not math.isfinite(accent) or not 0 <= accent <= 1:
        accent = None
    variants = {}
    for candidate in CANDIDATES:
        names = (*CLASSIFIERS, candidate)
        scores = [values[name] for name in names]
        variants[candidate] = {
            "models": list(names),
            "score_index_0_100": round(sum(scores) / len(scores), 2) if all(x is not None for x in scores) else None,
            "method": "arithmetic_mean_of_three_unvalidated_classifier_scores",
            "calibrated_ai_probability": None,
            "complete": all(x is not None for x in scores)
        }
    return {"model_scores_0_100": values,
            "ai_accent_style_index_0_100": round(accent * 100, 2) if accent is not None else None,
            "variants": variants, "publication_authorized": False}

def inspect_article(path):
    article = json.loads(path.read_text(encoding="utf-8"))
    text = extract_text(article.get("body_html", ""))
    if not text:
        raise ValueError("Empty article: " + path.name)
    with tempfile.TemporaryDirectory(prefix="compare-aiw-") as tmp:
        file = Path(tmp) / "article.txt"
        file.write_text(text, encoding="utf-8")
        raw = {name: tool_result(["aiw-ru", "classify", "--model", name, "--json"], file, timeout=300)
               for name in ALL_MODELS}
        raw["ai_accent_detector"] = tool_result(["ai-accent-detector", "--json", "--lang", "ru"], file)
    result = merge_results(raw)
    result.update({"schema": "aiw-replacement-comparison-v1",
         "article_slug": article.get("slug", path.stem),
         "channel": article.get("channel"),
         "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
         "characters_without_spaces": len(re.sub(r"\s", "", text)),
         "checked_at": datetime.now(timezone.utc).isoformat(),
         "status": "completed" if all(x["complete"] for x in result["variants"].values())
                   and result["ai_accent_style_index_0_100"] is not None else "incomplete",
         "errors": {name: str(raw[name].get("reason", raw[name].get("exit_code", "invalid_score")))[:240]
                    for name in raw if name in ALL_MODELS and score_from_tool(raw[name]) is None},
         "note": "Index is a descriptive average of correlated, uncalibrated models; NOT an authorship percentage."})
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="aiw-replacement-reports")
    parser.add_argument("--slug", action="append")
    args = parser.parse_args()
    paths = list(dict.fromkeys(p for slug in args.slug for p in selected_articles(slug))) if args.slug else selected_articles()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for p in paths:
        result = inspect_article(p)
        records.append(result)
        (output / p.name).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for key in CANDIDATES:
            print("MODEL_COMPARISON " + json.dumps({"slug": result["article_slug"],"channel":result["channel"],
               "candidate":key,"index_0_100":result["variants"][key]["score_index_0_100"],
               "model_scores":result["model_scores_0_100"],"accent_style":result["ai_accent_style_index_0_100"],
               "status":result["status"]}, ensure_ascii=False),flush=True)
    summary = {"schema":"aiw-replacement-comparison-index-v1", "article_count":len(records),
       "status":"complete" if records and all(r["status"]=="completed" for r in records) else "incomplete",
       "rows":[{"slug":r["article_slug"], "channel":r["channel"],
                "transformer":r["variants"]["transformer"]["score_index_0_100"],
                "mini_frida":r["variants"]["mini-frida"]["score_index_0_100"],
                "accent_style":r["ai_accent_style_index_0_100"]} for r in records],
       "publication_authorized":False}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",encoding="utf-8")
    print("COMPARISON_SUMMARY "+json.dumps(summary,ensure_ascii=False),flush=True)
    if summary["status"] != "complete":
        raise SystemExit("Comparison incomplete. Never infer missing model scores.")

if __name__=="__main__":
    main()
