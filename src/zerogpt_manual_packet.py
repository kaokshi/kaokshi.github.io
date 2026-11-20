#!/usr/bin/env python3
"""Prepare article text for HUMAN, free-site ZeroGPT checking (no website requests).
Read-only. Never supplies detector scores or authorizes publication.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from editorial_ai_check import extract_text, selected_articles

def clean_article(article):
    text = extract_text(article.get("body_html", ""))
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()

def prepare(slug, output):
    matched = selected_articles(slug)
    if len(matched) != 1:
        raise ValueError("Expected precisely one article")
    article = json.loads(matched[0].read_text(encoding="utf-8"))
    text = clean_article(article)
    if not text:
        raise ValueError("Article text is empty")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    filename = matched[0].stem
    (output / (filename + ".txt")).write_text(text + "\n", encoding="utf-8")
    manifest = {
        "schema": "zerogpt-manual-review-v1",
        "article_slug": article.get("slug", filename),
        "channel": article.get("channel"),
        "source_file": str(matched[0].as_posix()),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "characters": len(text),
        "characters_without_spaces": len(re.sub(r"\s", "", text)),
        "external_check": "not_performed",
        "external_score": None,
        "manual_check_url": "https://www.zerogpt.com/",
        "publication_authorized": False,
        "instructions": "Open ZeroGPT manually and paste the TXT text or upload the file. Record the displayed result separately, with the exact text SHA-256. Do not use browser automation."
    }
    (output / (filename + ".manifest.json")).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output", default="zerogpt-manual-packets")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", args.slug):
        parser.error("Unsafe slug")
    result = prepare(args.slug, args.output)
    print(json.dumps({"status": "ready_for_human_check",
                      "article_slug": result["article_slug"],
                      "characters": result["characters"],
                      "sha256": result["text_sha256"],
                      "publication_authorized": False}, ensure_ascii=False))

if __name__ == "__main__":
    main()
