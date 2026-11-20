#!/usr/bin/env python3
"""Optional, manually triggered Sapling comparison. Never approves publication."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from editorial_ai_check import extract_text, selected_articles

ENDPOINT = "https://api.sapling.ai/api/v1/aidetect"
MAX_CHARS = 10000

def prepare(article, max_chars=MAX_CHARS):
    if not 300 <= max_chars <= MAX_CHARS:
        raise ValueError("max_chars must be between 300 and 10000")
    text = extract_text(article.get("body_html", ""))
    text = text[:max_chars]
    if len(text) < 300:
        raise ValueError("Need at least 300 text characters")
    return text

def score_text(text, key, transport=urlopen):
    if not key:
        raise ValueError("SAPLING_API_KEY missing; no request made")
    if not 300 <= len(text) <= MAX_CHARS:
        raise ValueError("Text length out of pilot range")
    body = json.dumps({"text": text, "sent_scores": True,
                       "zero_retention": True, "create_report": False}).encode("utf-8")
    req = Request(ENDPOINT, data=body,
                  headers={"Content-Type": "application/json",
                           "Authorization": "Bearer " + key},
                  method="POST")
    try:
        with transport(req, timeout=90) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError) as error:
        # Do not dump upstream error bodies: they can contain user text.
        raise RuntimeError("Sapling request failed: " + type(error).__name__) from None
    score = payload.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
        raise ValueError("Sapling returned an invalid document score")
    return {"score_0_to_1": score, "verdict": payload.get("verdict"),
            "model_version": payload.get("version"), "language": payload.get("language"),
            "low_confidence": payload.get("low_confidence"),
            "sentence_scores": [{"start": s.get("start"), "end": s.get("end"),
                                 "score": s.get("score")}
                                for s in payload.get("sentence_scores", []) if isinstance(s, dict)]}

def report(article, text, result):
    return {"schema": "sapling-external-diagnostic-v1",
            "article_slug": article.get("slug"), "channel": article.get("channel"),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "characters_sent": len(text), "external": result,
            "not_ground_truth": True, "not_a_publication_gate": True,
            "publication_authorized": False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True, help="Exactly one existing article")
    parser.add_argument("--max-chars", type=int, default=1900)
    parser.add_argument("--output", default="sapling-diagnostic-reports")
    args = parser.parse_args()
    selected = selected_articles(args.slug)
    if len(selected) != 1:
        parser.error("Expected exactly one article")
    article = json.loads(selected[0].read_text(encoding="utf-8"))
    text = prepare(article, args.max_chars)
    key = os.getenv("SAPLING_API_KEY", "")
    result = score_text(text, key)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / (selected[0].stem + ".json")
    target.write_text(json.dumps(report(article, text, result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "completed", "article": selected[0].stem,
                      "characters_sent": len(text), "output": str(target),
                      "publication_authorized": False}))

if __name__ == "__main__":
    main()
